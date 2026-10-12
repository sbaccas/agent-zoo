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
                 stats: Optional[dict] = None, entity_events: Optional[dict] = None,
                 transmissions: Optional[list] = None,
                 pending_repositions: Optional[dict] = None):
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
        self.transmissions = [dict(t) for t in (transmissions or [])]
        self.pending_repositions = dict(pending_repositions or {})


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
        self.replay_snapshots: list[dict] = []

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
            transmissions=list(self.scheduler.transmissions),
            pending_repositions=dict(self.scheduler.pending_repositions),
        )
        self.history.append(snapshot)
        if len(self.history) > self.max_history:
            self.history.pop(0)

        # Full-match replay history: compact record of every entity for seamless archiving
        self.replay_snapshots.append({
            "tick": self.scheduler.tick,
            "round": self.scheduler.round,
            "captures": self.scheduler.captures,
            "scores": dict(self.scheduler.scores),
            "entities": [
                {
                    "id": e.id,
                    "type": getattr(getattr(e, "type", None), "value", str(getattr(e, "type", ""))),
                    "x": getattr(getattr(e, "position", None), "x", None),
                    "y": getattr(getattr(e, "position", None), "y", None),
                    "facing": getattr(getattr(e, "facing", None), "value", str(getattr(e, "facing", "north"))),
                    "hp": getattr(e, "hp", 100),
                    "alive": getattr(e, "alive", True),
                }
                for e in self.entity_manager.entities.values()
            ],
        })

    def get_state(self) -> SimulationStateResponse:
        pending = {
            eid: {"x": pos[0], "y": pos[1]}
            for eid, pos in self.scheduler.pending_repositions.items()
        } if self.scheduler.pending_repositions else None

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
            pending_repositions=pending,
        )

    def get_boxscore(self) -> dict:
        """Per-cat KPI summary for the box score window.

        `leader` is decided on kills alone and left None on a tie, so the UI
        never has to invent a tie-break the game does not define.
        """
        # Reconcile any uncredited absorbed kills from predator death records
        if hasattr(self.scheduler, "deaths") and self.stats:
            cat_cfg = self.entity_manager.species_config.get("cat")
            if cat_cfg is None or getattr(cat_cfg, "absorption", True):
                for d in self.scheduler.deaths:
                    if d.get("type") == "cat":
                        killer_id = d.get("killed_by")
                        victim_id = d.get("id")
                        if killer_id and victim_id and killer_id != victim_id:
                            if d.get("absorbed_score", 0) == 0:
                                victim_stat = self.stats.cats.get(victim_id)
                                if victim_stat:
                                    v_score = (
                                        victim_stat.absorbed_kills
                                        if (victim_id in ("cat-3", "evil-cat") and victim_stat.absorbed_kills > 0)
                                        else (victim_stat.kills + victim_stat.absorbed_kills)
                                    )
                                else:
                                    v_score = self.scheduler.scores.get(victim_id, 0)
                                if v_score > 0:
                                    mult = 2 if killer_id in ("cat-3", "evil-cat") else 1
                                    d["absorbed_score"] = mult * v_score

                for cat_id, cat_stat in self.stats.cats.items():
                    total_absorbed = sum(
                        d.get("absorbed_score", 0)
                        for d in self.scheduler.deaths
                        if d.get("type") == "cat" and d.get("killed_by") == cat_id
                    )
                    if total_absorbed > cat_stat.absorbed_kills:
                        diff = total_absorbed - cat_stat.absorbed_kills
                        cat_stat.absorbed_kills = total_absorbed
                        self.scheduler.scores[cat_id] = self.scheduler.scores.get(cat_id, 0) + diff

        cats = self.stats.build_boxscore(self.entity_manager, self.scheduler.tick)

        leader = None
        if cats:
            best = max(c.get("score", c["kills"]) for c in cats)
            leaders = [c["id"] for c in cats if c.get("score", c["kills"]) == best]
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
            mice_initial = (self.config.populations or {}).get("mice", 250)
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
                if self.scheduler.game_over:
                    self.status = SimulationStatus.COMPLETED
                    logger.info("Simulation %s completed at tick %s (winner: %s)", self.simulation_id, self.scheduler.tick, self.scheduler.winner_id)
                    break
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
                if self.scheduler.game_over:
                    self.status = SimulationStatus.COMPLETED
        return self.get_state()

    async def step_back(self) -> SimulationStateResponse:
        async with self._lock:
            if not self.history:
                return self.get_state()

            if self.status in (SimulationStatus.RUNNING, SimulationStatus.COMPLETED):
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
            self.scheduler.transmissions = [dict(t) for t in snapshot.transmissions]
            self.entity_manager.entities = {
                e.id: e.model_copy(deep=True) for e in snapshot.entities
            }
            self.action_handler.hazard_zones = [
                z.model_copy(deep=True) for z in snapshot.hazard_zones
            ]
            self.stats.restore(snapshot.stats)
            self.scheduler.pending_repositions = dict(snapshot.pending_repositions or {})
            self.scheduler.game_over = False
            self.scheduler.winner_id = None
            msg = f"⏪ [STEP BACK] Rewound to Tick {snapshot.tick} | Round {snapshot.round} | Captures: {snapshot.captures}"
            logger.info(msg)
            print(msg, flush=True)

        return self.get_state()

    async def seek_to_tick(self, target_tick: int) -> SimulationStateResponse:
        """Rewind the simulation state to a target historical tick in history."""
        async with self._lock:
            if not self.history:
                return self.get_state()

            if self.status in (SimulationStatus.RUNNING, SimulationStatus.COMPLETED):
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
                self.scheduler.transmissions = [dict(t) for t in target_snapshot.transmissions]
                self.entity_manager.entities = {
                    e.id: e.model_copy(deep=True) for e in target_snapshot.entities
                }
                self.action_handler.hazard_zones = [
                    z.model_copy(deep=True) for z in target_snapshot.hazard_zones
                ]
                self.stats.restore(target_snapshot.stats)
                self.scheduler.pending_repositions = dict(target_snapshot.pending_repositions or {})
                self.scheduler.game_over = False
                self.scheduler.winner_id = None
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
            self.replay_snapshots.clear()
            self.scheduler.tick = 0
            self.scheduler.round = 0
            self.scheduler.captures = 0
            self.scheduler.scores.clear()
            self.scheduler.events.clear()
            self.scheduler.entity_events.clear()
            self.scheduler.transmissions.clear()
            self.scheduler.pending_repositions.clear()
            self.scheduler.deaths.clear()
            self.scheduler.notified_deaths.clear()
            self.scheduler.consecutive_http_errors.clear()
            self.scheduler.known_agent_ids.clear()
            self.scheduler.game_over = False
            self.scheduler.winner_id = None
            self.stats.reset()
            self.status = SimulationStatus.CREATED

            # Reset on-disk and in-memory actor stores directly (including cat-3 / Evil Cat)
            try:
                from actors.cat import store as cat_store
                from actors.evil_cat import store as evil_store
                from actors.a2a import store as a2a_store
                from actors.cat.subagents.eval import (
                    bump_eval_reset_epoch as bump_cat_eval_epoch,
                    clear_coach_directive as clear_cat_coach_directive,
                )
                from actors.evil_cat.eval import (
                    bump_eval_reset_epoch as bump_evil_eval_epoch,
                    clear_coach_directive as clear_evil_coach_directive,
                )
                bump_cat_eval_epoch()
                bump_evil_eval_epoch()
                cat_store.reset_transcript("all")
                cat_store.reset_coaching_notes("all")
                cat_store.reset_coach_memories("all")
                cat_store.reset_prompts("all")
                cat_store.reset_notice_board()
                evil_store.reset_transcript("cat-3")
                evil_store.reset_coaching_notes("cat-3")
                evil_store.reset_coach_memories("cat-3")
                evil_store.reset_prompts("cat-3")
                a2a_store.clear_a2a_history("all")
                clear_cat_coach_directive()
                clear_evil_coach_directive()
            except Exception as exc:
                logger.warning(f"Error resetting local agent stores during simulation reset: {exc}")

            # Notify active agent HTTP servers if running (8001, 8003, 8005, plus any CAT_PEER_REGISTRY URLs)
            async def _notify_agents_reset():
                import httpx
                target_urls = {
                    "http://127.0.0.1:8001/api/agent/reset",
                    "http://127.0.0.1:8003/api/agent/reset",
                    "http://127.0.0.1:8005/api/agent/reset",
                }
                try:
                    from actors.a2a.client import get_peer_registry
                    for base_url in get_peer_registry().values():
                        if base_url:
                            target_urls.add(f"{base_url.rstrip('/')}/api/agent/reset")
                except Exception:
                    pass
                async with httpx.AsyncClient(timeout=0.8) as client:
                    for url in sorted(target_urls):
                        try:
                            await client.post(url)
                        except Exception:
                            pass
            asyncio.create_task(_notify_agents_reset())

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

    def stage_reposition(self, entity_id: str, x: int, y: int) -> dict:
        """Stage an entity to be repositioned to (x, y) on the next simulation tick."""
        ent = self.entity_manager.entities.get(entity_id)
        if not ent or not ent.alive:
            raise ValueError(f"Entity '{entity_id}' not found or is dead.")
        if not self.world.is_in_bounds(x, y):
            raise ValueError(f"Target position ({x}, {y}) is out of world bounds (0..{self.world.width-1}, 0..{self.world.height-1}).")
        if not self.world.is_traversable(x, y):
            raise ValueError(f"Target position ({x}, {y}) is blocked by an obstacle.")

        if ent.position.x == x and ent.position.y == y:
            self.scheduler.pending_repositions.pop(entity_id, None)
            return {
                "entity_id": entity_id,
                "staged_position": {"x": x, "y": y},
                "cancelled": True,
                "effective_tick": self.scheduler.tick,
            }

        self.scheduler.pending_repositions[entity_id] = (x, y)
        return {
            "entity_id": entity_id,
            "staged_position": {"x": x, "y": y},
            "effective_tick": self.scheduler.tick + 1,
        }

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

    def get_comms_log(self, limit: int = 100) -> list[dict]:
        """Returns the rolling inter-cat radio transmission log."""
        return list(self.scheduler.transmissions[-limit:])
