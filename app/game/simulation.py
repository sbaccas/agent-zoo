import asyncio
import logging
import time
import uuid
from typing import Optional
from app.game.actions import ActionHandler
from app.game.archive import archive_game
from app.game.entities import EntityManager
from app.game import heatmap
from app.game.scheduler import Scheduler
from app.game.stats import StatsTracker
from app.game.world import World
from app.models.entity import EntityType
from app.models.simulation import (
    SimulationConfig,
    SimulationStateResponse,
    SimulationStatus,
)

logger = logging.getLogger("simulation")


class SimulationSnapshot:
    def __init__(self, tick: int, round: int, captures: int, entities: list, events: list,
                 scores: Optional[dict] = None, hazard_zones: Optional[list] = None,
                 stats: Optional[dict] = None, entity_events: Optional[dict] = None):
        self.tick = tick
        self.round = round
        self.captures = captures
        self.entities = entities
        self.events = events
        # Per-cat scores must rewind with everything else, or a step-back leaves
        # scores inflated while the mice come back to life.
        self.scores = dict(scores or {})
        # Likewise for live zones: rewinding past a slash must take its lethal
        # tiles with it, otherwise the board keeps killing on behalf of an
        # attack that no longer happened.
        self.hazard_zones = list(hazard_zones or [])
        # And likewise for the box score, which would otherwise keep crediting
        # slashes and ground that the rewind has just undone. Cheap to hold:
        # the tracker hands over plain integers, including a length marker
        # standing in for the visited-tile set.
        self.stats = dict(stats or {})
        self.entity_events = {k: list(v) for k, v in (entity_events or {}).items()}


class Simulation:
    def __init__(self, config: Optional[SimulationConfig] = None, simulation_id: Optional[str] = None):
        self.simulation_id = simulation_id or f"sim-{uuid.uuid4().hex[:8]}"
        self.config = config or SimulationConfig()
        self.world = World(
            width=self.config.width,
            height=self.config.height,
            topology=self.config.topology,
            generate_map=getattr(self.config, "enable_map_features", False),
        )
        self.entity_manager = EntityManager(
            world=self.world,
            species_config=self.config.species,
        )
        # One tracker, shared by both collaborators. The action handler records
        # what each cat did; the scheduler records the ticks it was too locked
        # to do anything at all.
        self.stats = StatsTracker(self.world)
        self.action_handler = ActionHandler(self.world, self.entity_manager, stats=self.stats)
        self.scheduler = Scheduler(self.world, self.entity_manager, self.action_handler, stats=self.stats)
        # Seeded here as well as in the run loop so the single-step and paused
        # paths advertise a window rather than sending none.
        self._sync_tick_budget()

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
            scores=dict(self.scheduler.scores),
            hazard_zones=[z.model_copy(deep=True) for z in self.action_handler.hazard_zones],
            stats=self.stats.snapshot(),
            entity_events={k: [dict(ev) for ev in v] for k, v in self.scheduler.entity_events.items()},
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
            hazard_zones=list(self.action_handler.hazard_zones),
            can_step_back=len(self.history) > 0,
            species=self.entity_manager.species_config,
            disclosure=self.config.disclosure,
            map_features=self.world.get_map_features(),
        )

    def get_boxscore(self) -> dict:
        """Per-cat KPI summary for the box score window.

        `leader` is decided on kills alone and left None on a tie, so the UI
        never has to invent a tie-break the game does not define.
        """
        cats = self.stats.build_boxscore(self.entity_manager, self.scheduler.tick)

        leader = None
        if cats:
            best = max(c["kills"] for c in cats)
            leaders = [c["id"] for c in cats if c["kills"] == best]
            if len(leaders) == 1:
                leader = leaders[0]

        res = {
            "simulation_id": self.simulation_id,
            "status": self.status,
            "tick": self.scheduler.tick,
            "round": self.scheduler.round,
            # Non-zero when counting began mid-game, e.g. the server restarted
            # under a game already in progress. The UI shows this as a caveat
            # rather than silently presenting partial totals as final ones.
            "tracking_since_tick": self.stats.since_tick,
            "arena": {
                "width": self.world.width,
                "height": self.world.height,
                "tiles": self.world.width * self.world.height,
            },
            "leader": leader,
            "cats": cats,
        }

        # Epistemic tier promotion: when coach_sees_population is True, include
        # board-level survival/depletion counters in the box score payload.
        if getattr(self.config, "disclosure", None) and self.config.disclosure.coach_sees_population:
            mice_alive = self.entity_manager.count_by_type(EntityType.MOUSE)
            mice_initial = (self.config.populations or {}).get("mice", 150)
            res["mice_alive"] = mice_alive
            res["mice_initial"] = mice_initial
            res["captures"] = self.scheduler.captures

        return res

    def get_heatmap(
        self, grid_size: int = heatmap.DEFAULT_GRID_SIZE, last_n: Optional[int] = None
    ) -> dict:
        """Per-cat movement map, downsampled for the popout and the coach.

        Derived on demand from `history` rather than maintained during play. The
        snapshots are already being taken for step-back, so this costs nothing
        until someone asks, and it inherits rewind correctness for free.
        """
        return heatmap.build(self, grid_size=grid_size, last_n=last_n)

    def _sync_tick_budget(self) -> None:
        """Tells the scheduler how long agents have between ticks.

        Derived rather than configured separately, so the promise made to the
        agent cannot drift away from the interval actually enforced.
        """
        self.scheduler.tick_budget_sec = 1.0 / max(0.1, self.config.tick_rate)

    async def _run_loop(self):
        try:
            while self.status == SimulationStatus.RUNNING:
                period = 1.0 / max(0.1, self.config.tick_rate)
                self._sync_tick_budget()
                started = time.monotonic()
                self._save_snapshot()
                await self.scheduler.execute_simulation_step()
                # Sleep the remainder, not the whole period. The step itself used
                # to block for up to the agent budget, so the real interval was
                # the configured one plus however long every agent took in turn,
                # and an agent promised a window of 1/tick_rate was quietly given
                # more. Now the step is cheap and the interval is what it says.
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, period - elapsed))
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
            self.scheduler.scores = dict(snapshot.scores)
            self.scheduler.events = list(snapshot.events)
            self.scheduler.entity_events = {k: list(v) for k, v in snapshot.entity_events.items()}
            self.entity_manager.entities = {
                e.id: e.model_copy(deep=True) for e in snapshot.entities
            }
            self.action_handler.hazard_zones = [
                z.model_copy(deep=True) for z in snapshot.hazard_zones
            ]
            self.stats.restore(snapshot.stats)
            msg = f"⏪ [STEP BACK] Rewound to Tick {snapshot.tick} | Round {snapshot.round} | Captures: {snapshot.captures}"
            logger.info(msg)
            print(msg, flush=True)

        return self.get_state()

    async def seek_to_tick(self, target_tick: int) -> SimulationStateResponse:
        """Rewind the simulation state to a target historical tick in history."""
        async with self._lock:
            if not self.history:
                return self.get_state()

            if self.status == SimulationStatus.RUNNING:
                self.status = SimulationStatus.PAUSED
                if self._loop_task and not self._loop_task.done():
                    self._loop_task.cancel()

            target_snapshot = None
            while self.history:
                snap = self.history.pop()
                if snap.tick <= target_tick:
                    target_snapshot = snap
                    break

            if target_snapshot is not None:
                self.scheduler.tick = target_snapshot.tick
                self.scheduler.round = target_snapshot.round
                self.scheduler.captures = target_snapshot.captures
                self.scheduler.scores = dict(target_snapshot.scores)
                self.scheduler.events = list(target_snapshot.events)
                self.scheduler.entity_events = {k: list(v) for k, v in target_snapshot.entity_events.items()}
                self.entity_manager.entities = {
                    e.id: e.model_copy(deep=True) for e in target_snapshot.entities
                }
                self.action_handler.hazard_zones = [
                    z.model_copy(deep=True) for z in target_snapshot.hazard_zones
                ]
                self.stats.restore(target_snapshot.stats)
                msg = f"⏪ [SEEK] Rewound to Tick {target_snapshot.tick} | Round {target_snapshot.round} | Captures: {target_snapshot.captures}"
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

            # Before anything is mutated. `stats.reset()` below is destructive
            # and the cat actor cannot save the box score on its own behalf: it
            # only learns a reset happened on a later poll, when it sees the
            # tick counter has gone backwards, and by then these counters read
            # zero. Awaited rather than backgrounded so the copy finishes while
            # the tick still reads its final value -- the actor wipes its own
            # runtime files the moment it notices the rewind.
            await archive_game(self, reason="reset")

            self.history.clear()
            self.scheduler.tick = 0
            self.scheduler.round = 0
            self.scheduler.captures = 0
            self.scheduler.scores.clear()
            self.scheduler.events.clear()
            self.scheduler.entity_events.clear()
            self.stats.reset()
            self.status = SimulationStatus.CREATED

            # Reset entity manager
            self.entity_manager = EntityManager(self.world, self.config.species)
            # The rebuilt handler must be handed the tracker again; without it
            # a reset game would look healthy but record nothing.
            self.action_handler = ActionHandler(self.world, self.entity_manager, stats=self.stats)
            self.scheduler.entity_manager = self.entity_manager
            self.scheduler.action_handler = self.action_handler

            cats = self.config.populations.get("cats", 2)
            mice = self.config.populations.get("mice", 10)
            self.entity_manager.spawn_population(EntityType.CAT, cats)
            self.entity_manager.spawn_population(EntityType.MOUSE, mice)

        return self.get_state()

    def set_tick_rate(self, tick_rate: float) -> float:
        self.config.tick_rate = max(0.1, min(100.0, tick_rate))
        # The budget travels to the agent on the next dispatch; without this a
        # speed change would leave agents working to the old window.
        self._sync_tick_budget()
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
