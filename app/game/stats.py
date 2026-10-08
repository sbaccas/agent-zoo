"""Per-cat performance counters behind the box score.

Why this lives in the engine rather than in the cat actor's transcript: the
transcript only exists for agents driven by the workbench, so it can describe
`cat-1` and is blind to the heuristic `cat-2` it is losing to. A box score is
only useful if both sides are measured by the same instrument, so the counting
happens where every cat is visible regardless of what is driving it.

Hook placement follows from the same reasoning. Action-derived counters are
recorded inside `ActionHandler.apply_action`, which is the one choke point every
control path funnels through -- scheduler loop, manual UI clicks, and the HTTP
agent endpoint alike. Counting in the scheduler instead would silently miss the
manual and HTTP paths. The single exception is `ticks_locked`: the scheduler
skips locked cats without ever calling `apply_action`, so that one counter has
to be driven from the loop.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.action import ActionType
from app.models.entity import Entity, EntityType

# Actions that translate directly into ground covered, and the displacement each
# is supposed to produce. Used to sanity-bound measured displacement so a torus
# wrap reads as the 1 or 2 tiles actually travelled instead of the ~width jump
# the raw coordinates would suggest.
_STEP_ACTIONS = {
    ActionType.NORTH: 1,
    ActionType.SOUTH: 1,
    ActionType.EAST: 1,
    ActionType.WEST: 1,
    ActionType.LUNGE: 2,
}


# The engine name the cat agent reports when a completed Gemini deliberation
# supplied the action. Anything else it reports is one of its own fast paths.
MODEL_ENGINE = "adk_gemini"


class CatStats:
    """Counters for a single cat.

    Every field is monotonic. That is what makes rewinding cheap: `visited` only
    ever grows, so a snapshot can record the *length* of `visited_order` as one
    integer and restoring means truncating back to it. Deep-copying the tile set
    on all 500 retained snapshots would otherwise cost millions of tuples on a
    long game.
    """

    def __init__(self, cat_id: str) -> None:
        self.cat_id = cat_id

        # Scoring
        self.kills = 0
        self.absorbed_kills = 0

        # Slashing. `landed_slashes` counts slashes that killed at least one
        # mouse at any point in their lethal window, which is why slash
        # attribution is tracked per zone below.
        self.slash_attempts = 0
        self.landed_slashes = 0
        self.multi_kills = 0

        # Opportunity conversion
        self.zone_chances = 0
        self.zone_attempts = 0

        # Movement
        self.tiles_run = 0
        self.visited_order: List[Tuple[int, int]] = []
        self.visited: Set[Tuple[int, int]] = set()
        self.moves = 0
        self.lunges = 0
        self.wall_bonks = 0

        # Tempo / positioning
        self.ticks_free = 0
        self.ticks_locked = 0
        self.prey_in_sight_ticks = 0

        # Who actually decided. Three distinct players can supply a cat's action
        # and only one of them is the language model:
        #   - the model, via a completed background deliberation;
        #   - the agent's own System-1 reflex, when no deliberation was ready;
        #   - the server-side heuristic, when the agent said nothing usable.
        # The latter two are strong players. The reflex pounces on anything in
        # the strike zone and the heuristic only ever swings on a guaranteed
        # kill, so a model that rarely speaks inherits their accuracy and reads
        # as if it had improved.
        self.decisions_by_model = 0
        self.decisions_by_agent_reflex = 0
        self.decisions_by_fallback = 0
        # Of the fallbacks, how many were an answer that arrived describing a
        # board that had already moved on. Separated from plain silence because
        # the two call for different fixes: a longer budget versus a faster agent.
        self.decisions_stale = 0

        # zone_id -> kills credited to that slash. Not a reported statistic; it
        # exists so a slash whose blade kills on the tick *after* it lands is
        # not filed as a whiff.
        self.zone_kills: Dict[str, int] = {}

    def visit(self, x: int, y: int) -> None:
        tile = (x, y)
        if tile not in self.visited:
            self.visited.add(tile)
            self.visited_order.append(tile)

    # -- rewind support -------------------------------------------------

    _COUNTERS = (
        "kills", "absorbed_kills", "slash_attempts", "landed_slashes", "multi_kills",
        "zone_chances", "zone_attempts", "tiles_run", "moves", "lunges",
        "wall_bonks", "ticks_free", "ticks_locked", "prey_in_sight_ticks",
        "decisions_by_model", "decisions_by_agent_reflex",
        "decisions_by_fallback", "decisions_stale",
    )

    def snapshot(self) -> Dict[str, Any]:
        snap: Dict[str, Any] = {name: getattr(self, name) for name in self._COUNTERS}
        snap["visited_len"] = len(self.visited_order)
        snap["zone_kills"] = dict(self.zone_kills)
        return snap

    def restore(self, snap: Dict[str, Any]) -> None:
        for name in self._COUNTERS:
            setattr(self, name, snap.get(name, 0))
        visited_len = snap.get("visited_len", 0)
        if visited_len < len(self.visited_order):
            self.visited_order = self.visited_order[:visited_len]
            self.visited = set(self.visited_order)
        self.zone_kills = dict(snap.get("zone_kills", {}))


class StatsTracker:
    """Owns one `CatStats` per cat and the hooks that feed them."""

    def __init__(self, world, since_tick: int = 0) -> None:
        self.world = world
        # A game already in flight when this tracker is attached would otherwise
        # present partial counters as if they covered the whole match. The box
        # score surfaces this so a half-measured game is not read as a bad one.
        self.since_tick = since_tick
        self.cats: Dict[str, CatStats] = {}

    def get(self, cat_id: str) -> CatStats:
        stats = self.cats.get(cat_id)
        if stats is None:
            stats = CatStats(cat_id)
            self.cats[cat_id] = stats
        return stats

    # -- hooks ----------------------------------------------------------

    def record_locked_tick(self, entity: Entity) -> None:
        """Called from the scheduler for a cat that is recovering and cannot act."""
        if entity.type != EntityType.CAT:
            return
        self.get(entity.id).ticks_locked += 1

    def record_decision_source(
        self, entity: Entity, status: str, engine: Optional[str] = None
    ) -> None:
        """Files who supplied this tick's action, to three levels of precision.

        Called only for entities that have an agent attached, so the denominator
        is "ticks where a model could have answered" rather than "ticks". A cat
        with no agent has no opinion to miss, and padding its row with fallbacks
        would read as a model failing when there was never a model.

        `engine` matters because an answer arriving on time does not mean the
        language model produced it. The cat agent runs a two-system design: it
        serves a completed background deliberation when one is ready and
        otherwise answers from its own fast reflex, and both reach the server
        within milliseconds. Counting arrivals alone would credit the model for
        every tick its reflex covered, which is the same mistake in a different
        place as crediting it for the server-side heuristic.
        """
        if entity.type != EntityType.CAT:
            return
        # Imported rather than spelled out: a rename on the transport side would
        # otherwise quietly file every model decision as a fallback.
        from app.game.ws_manager import COLLECT_FRESH, COLLECT_STALE

        stats = self.get(entity.id)
        if status == COLLECT_FRESH:
            # An agent that declares no engine is taken at its word as a model
            # answer; only a self-declared reflex is filed as one. Guessing the
            # other way would mute every third-party agent that does not know
            # about this field.
            if engine is not None and engine != MODEL_ENGINE:
                stats.decisions_by_agent_reflex += 1
            else:
                stats.decisions_by_model += 1
            return
        stats.decisions_by_fallback += 1
        if status == COLLECT_STALE:
            stats.decisions_stale += 1

    def record_action(
        self,
        entity: Entity,
        action: ActionType,
        accepted: bool,
        prev_x: int,
        prev_y: int,
        new_x: int,
        new_y: int,
        hits: int,
        zone_id: Optional[str],
        had_zone_chance: bool,
        prey_visible: bool,
    ) -> None:
        """Record one resolved action. Cats only; mice are not scored."""
        if entity.type != EntityType.CAT:
            return

        stats = self.get(entity.id)
        stats.ticks_free += 1
        stats.visit(prev_x, prev_y)

        if prey_visible:
            stats.prey_in_sight_ticks += 1
        if had_zone_chance:
            stats.zone_chances += 1
            if action == ActionType.ATTACK:
                stats.zone_attempts += 1

        if not accepted:
            return

        if action == ActionType.ATTACK:
            stats.slash_attempts += 1
            if hits >= 1:
                stats.landed_slashes += 1
            if hits >= 2:
                stats.multi_kills += 1
            if zone_id is not None:
                # Seed the attribution slot so a later zone kill can find it.
                stats.zone_kills.setdefault(zone_id, 0)
                if hits:
                    stats.zone_kills[zone_id] = stats.zone_kills.get(zone_id, 0) + hits
            return

        intended = _STEP_ACTIONS.get(action)
        if intended is None:
            return

        travelled = min(abs(new_x - prev_x) + abs(new_y - prev_y), intended)
        stats.tiles_run += travelled
        stats.visit(new_x, new_y)

        if action == ActionType.LUNGE:
            stats.lunges += 1
            # A lunge is meant to cover 2 tiles; anything less means it clipped
            # the arena boundary, which is the behaviour the LLM cat has been
            # over-generalising into a blanket distrust of lunging.
            if travelled < intended:
                stats.wall_bonks += 1
        else:
            stats.moves += 1

    def record_kill(
        self,
        cat_id: str,
        zone_id: Optional[str] = None,
        absorbed_kills: int = 0,
        is_evil: bool = False,
        is_cat: bool = False,
    ) -> None:
        stats = self.get(cat_id)
        if is_evil:
            if is_cat:
                stats.absorbed_kills += max(0, absorbed_kills)
            # Evil cat receives 0 score/kills from mice; cat kill score is 2x victim consumed score
        else:
            stats.kills += 1
            stats.absorbed_kills += max(0, absorbed_kills)
        if zone_id is None:
            return
        # A kill from a lingering blade retroactively promotes its slash out of
        # the whiff column, but only the first one: the slash is either landed
        # or it is not.
        if zone_id in stats.zone_kills:
            if stats.zone_kills[zone_id] == 0:
                stats.landed_slashes += 1
            stats.zone_kills[zone_id] += 1
            if stats.zone_kills[zone_id] == 2:
                stats.multi_kills += 1

    # -- lifecycle ------------------------------------------------------

    def reset(self, since_tick: int = 0) -> None:
        self.cats.clear()
        self.since_tick = since_tick

    def snapshot(self) -> Dict[str, Any]:
        return {cid: s.snapshot() for cid, s in self.cats.items()}

    def restore(self, snap: Dict[str, Any]) -> None:
        # Cats absent from the snapshot predate it; zero them rather than
        # leaving post-rewind counters standing.
        for cid, stats in self.cats.items():
            if cid not in snap:
                stats.restore({})
        for cid, cat_snap in snap.items():
            self.get(cid).restore(cat_snap)

    # -- reporting ------------------------------------------------------

    def build_boxscore(self, entity_manager, tick: int) -> List[Dict[str, Any]]:
        """Render per-cat counters into the reported KPI set.

        Rates are emitted as fractions in 0..1 and formatted by the client, and
        any rate with an empty denominator is emitted as None so the UI can show
        an explicit dash instead of a fabricated zero.
        """
        arena_tiles = max(1, self.world.width * self.world.height)
        elapsed = max(0, tick - self.since_tick)

        rows: List[Dict[str, Any]] = []
        cats = [e for e in entity_manager.entities.values() if e.type == EntityType.CAT]
        for cat in sorted(cats, key=lambda c: c.id):
            s = self.get(cat.id)
            unique_tiles = len(s.visited_order)
            traversals = s.moves + s.lunges
            total_ticks = s.ticks_free + s.ticks_locked
            decisions_total = (
                s.decisions_by_model
                + s.decisions_by_agent_reflex
                + s.decisions_by_fallback
            )

            rows.append({
                "id": cat.id,
                "mode": entity_manager.get_agent_mode(cat.id),
                "alive": cat.alive,
                "hp": getattr(cat, "hp", 100),
                "max_hp": getattr(cat, "max_hp", 100),
                "position": {"x": cat.position.x, "y": cat.position.y},

                "kills": s.kills,
                "absorbed_kills": s.absorbed_kills,
                "score": s.kills + s.absorbed_kills,
                "kills_per_100_ticks": _rate(s.kills * 100, elapsed),
                "tiles_per_kill": _rate(s.tiles_run, s.kills),

                "slash_attempts": s.slash_attempts,
                "swings": s.slash_attempts,
                "slash_accuracy": _rate(s.landed_slashes, s.slash_attempts),
                "accuracy_pct": _rate(s.landed_slashes, s.slash_attempts),
                "landed_slashes": s.landed_slashes,
                "hits": s.landed_slashes,
                "whiffs": s.slash_attempts - s.landed_slashes,
                "multi_kills": s.multi_kills,
                "multikills": s.multi_kills,

                "zone_chances": s.zone_chances,
                "zone_conversion": _rate(s.zone_attempts, s.zone_chances),
                "conversion_pct": _rate(s.zone_attempts, s.zone_chances),
                "zone_declined": s.zone_chances - s.zone_attempts,

                "tiles_run": s.tiles_run,
                "distance_traveled": s.tiles_run,
                "unique_tiles": unique_tiles,
                "ground_covered": unique_tiles / arena_tiles,
                "speed_tiles_per_tick": _rate(s.tiles_run, elapsed),
                # Ticks spent per distinct tile occupied: a loitering measure,
                # not a path-retracing one. Named for the formula so the label
                # cannot invite the wrong reading.
                "ticks_per_tile": _rate(total_ticks, unique_tiles),

                "moves": s.moves,
                "lunges": s.lunges,
                "lunge_share": _rate(s.lunges, traversals),
                "wall_bonks": s.wall_bonks,

                "ticks_free": s.ticks_free,
                "ticks_locked": s.ticks_locked,
                "locked_pct": _rate(s.ticks_locked, total_ticks),
                "prey_in_sight_pct": _rate(s.prey_in_sight_ticks, s.ticks_free),

                # Decision provenance. Null for a cat with no agent attached:
                # there was no model to answer, so there is no share to report.
                "decisions_by_model": s.decisions_by_model,
                "decisions_autonomous": s.decisions_by_model,
                "decisions_by_agent_reflex": s.decisions_by_agent_reflex,
                "decisions_by_fallback": s.decisions_by_fallback,
                "decisions_stale": s.decisions_stale,
                "model_control_pct": _rate(s.decisions_by_model, decisions_total),
            })
        return rows


def _rate(numerator: float, denominator: float) -> Optional[float]:
    """Division that reports 'no data' rather than inventing a zero."""
    if not denominator:
        return None
    return numerator / denominator
