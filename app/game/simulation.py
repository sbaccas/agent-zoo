import asyncio
import logging
import uuid
from typing import Optional
from app.game.actions import ActionHandler
from app.game.entities import EntityManager
from app.game.scheduler import Scheduler
from app.game.world import World
from app.models.entity import EntityType
from app.models.simulation import (
    SimulationConfig,
    SimulationStateResponse,
    SimulationStatus,
)

logger = logging.getLogger("simulation")


class SimulationSnapshot:
    def __init__(self, tick: int, round: int, captures: int, entities: list, events: list):
        self.tick = tick
        self.round = round
        self.captures = captures
        self.entities = entities
        self.events = events


class Simulation:
    def __init__(self, config: Optional[SimulationConfig] = None, simulation_id: Optional[str] = None):
        self.simulation_id = simulation_id or f"sim-{uuid.uuid4().hex[:8]}"
        self.config = config or SimulationConfig()
        self.world = World(
            width=self.config.width,
            height=self.config.height,
            topology=self.config.topology,
        )
        self.entity_manager = EntityManager(
            world=self.world,
            species_config=self.config.species,
        )
        self.action_handler = ActionHandler(self.world, self.entity_manager)
        self.scheduler = Scheduler(self.world, self.entity_manager, self.action_handler)

        self.status = SimulationStatus.CREATED
        self._loop_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.history: list[SimulationSnapshot] = []
        self.max_history: int = 500

        # Initialize initial populations
        cats = self.config.populations.get("cats", 2)
        mice = self.config.populations.get("mice", 10)
        self.entity_manager.spawn_population(EntityType.CAT, cats)
        self.entity_manager.spawn_population(EntityType.MOUSE, mice)

    @property
    def tick(self) -> int:
        return self.scheduler.tick

    @property
    def round(self) -> int:
        return self.scheduler.round

    def _save_snapshot(self):
        snapshot = SimulationSnapshot(
            tick=self.scheduler.tick,
            round=self.scheduler.round,
            captures=self.scheduler.captures,
            entities=[e.model_copy(deep=True) for e in self.entity_manager.entities.values()],
            events=list(self.scheduler.events),
        )
        self.history.append(snapshot)
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def get_state(self) -> SimulationStateResponse:
        return SimulationStateResponse(
            simulation_id=self.simulation_id,
            status=self.status,
            tick=self.scheduler.tick,
            round=self.scheduler.round,
            width=self.world.width,
            height=self.world.height,
            topology=self.world.topology,
            tick_rate=self.config.tick_rate,
            cat_count=self.entity_manager.count_by_type(EntityType.CAT),
            mouse_count=self.entity_manager.count_by_type(EntityType.MOUSE),
            captures=self.scheduler.captures,
            entities=self.entity_manager.get_alive_entities(),
            recent_events=self.scheduler.events[-25:],
            can_step_back=len(self.history) > 0,
            species=self.entity_manager.species_config,
        )

    async def _run_loop(self):
        try:
            while self.status == SimulationStatus.RUNNING:
                self._save_snapshot()
                await self.scheduler.execute_simulation_step()
                sleep_sec = 1.0 / max(0.1, self.config.tick_rate)
                await asyncio.sleep(sleep_sec)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("Error in simulation runner loop: %s", e)
            self.status = SimulationStatus.STOPPED

    async def start(self) -> SimulationStateResponse:
        async with self._lock:
            if self.status in [SimulationStatus.CREATED, SimulationStatus.PAUSED, SimulationStatus.STOPPED]:
                self.status = SimulationStatus.RUNNING
                if self._loop_task is None or self._loop_task.done():
                    self._loop_task = asyncio.create_task(self._run_loop())
        return self.get_state()

    async def pause(self) -> SimulationStateResponse:
        async with self._lock:
            if self.status == SimulationStatus.RUNNING:
                self.status = SimulationStatus.PAUSED
                if self._loop_task and not self._loop_task.done():
                    self._loop_task.cancel()
        return self.get_state()

    async def resume(self) -> SimulationStateResponse:
        return await self.start()

    async def step(self) -> SimulationStateResponse:
        async with self._lock:
            if self.status != SimulationStatus.RUNNING:
                self._save_snapshot()
                await self.scheduler.execute_simulation_step()
        return self.get_state()

    async def step_back(self) -> SimulationStateResponse:
        async with self._lock:
            if not self.history:
                return self.get_state()

            if self.status == SimulationStatus.RUNNING:
                self.status = SimulationStatus.PAUSED
                if self._loop_task and not self._loop_task.done():
                    self._loop_task.cancel()

            snapshot = self.history.pop()
            self.scheduler.tick = snapshot.tick
            self.scheduler.round = snapshot.round
            self.scheduler.captures = snapshot.captures
            self.scheduler.events = list(snapshot.events)
            self.entity_manager.entities = {
                e.id: e.model_copy(deep=True) for e in snapshot.entities
            }
            msg = f"⏪ [STEP BACK] Rewound to Tick {snapshot.tick} | Round {snapshot.round} | Captures: {snapshot.captures}"
            logger.info(msg)
            print(msg, flush=True)

        return self.get_state()

    async def stop(self) -> SimulationStateResponse:
        async with self._lock:
            self.status = SimulationStatus.STOPPED
            if self._loop_task and not self._loop_task.done():
                self._loop_task.cancel()
        return self.get_state()

    async def reset(self) -> SimulationStateResponse:
        async with self._lock:
            if self._loop_task and not self._loop_task.done():
                self._loop_task.cancel()

            self.history.clear()
            self.scheduler.tick = 0
            self.scheduler.round = 0
            self.scheduler.captures = 0
            self.scheduler.events.clear()
            self.status = SimulationStatus.CREATED

            # Reset entity manager
            self.entity_manager = EntityManager(self.world, self.config.species)
            self.action_handler = ActionHandler(self.world, self.entity_manager)
            self.scheduler.entity_manager = self.entity_manager
            self.scheduler.action_handler = self.action_handler

            cats = self.config.populations.get("cats", 2)
            mice = self.config.populations.get("mice", 10)
            self.entity_manager.spawn_population(EntityType.CAT, cats)
            self.entity_manager.spawn_population(EntityType.MOUSE, mice)

        return self.get_state()

    def set_tick_rate(self, tick_rate: float) -> float:
        self.config.tick_rate = max(0.1, min(100.0, tick_rate))
        return self.config.tick_rate

    def modify_population(self, species: str, delta: int) -> int:
        if species.lower() in ["cat", "cats"]:
            entity_type = EntityType.CAT
        elif species.lower() in ["mouse", "mice"]:
            entity_type = EntityType.MOUSE
        else:
            raise ValueError(f"Unknown species: {species}")

        if delta > 0:
            self.entity_manager.spawn_population(entity_type, delta)
        elif delta < 0:
            self.entity_manager.remove_population(entity_type, abs(delta))

        return self.entity_manager.count_by_type(entity_type)
