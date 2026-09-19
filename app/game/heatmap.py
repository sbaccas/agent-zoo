"""Downsampled movement maps for the cats, sized for a language model's context.

Why this module reads history instead of counting during play
-------------------------------------------------------------
The positions were already being recorded. ``Simulation._save_snapshot`` deep
copies every entity once per tick so that step-back can restore them, which
leaves ``Simulation.history`` holding a rolling per-tick record of where every
cat stood. Counting tiles a second time inside the tick loop would duplicate
that at a cost to the hot path, and would need its own rewind handling to avoid
reporting ground the player has just undone. Reading the snapshots instead is
free on both counts: the rewind already pops them, so the map rewinds with the
game without this module knowing rewind exists.

The one thing history cannot answer is "never". It is capped at
``max_history`` ticks, so on a long game its oldest ticks are gone and a tile
absent from it may simply have been visited earlier. ``CatStats.visited`` is
uncapped and never forgets, so the NEVER VISITED line is taken from there and
the percentages from history. Each source covers the other's blind spot.

Why the digits sum to 100
-------------------------
The digit in a cell is the percentage of ticks the cat spent in that cell, and
the grid sums to exactly 100. That makes the map a distribution rather than an
intensity: a reader can say "this cell is a fifth of the cat's life" without a
legend, and two cats can be compared without normalising anything.

Why the grid is so coarse
-------------------------
The map is sized in *cells per side*, 2 to 5, defaulting to 5. On the default
100x100 arena that is 25 cells of 20x20 tiles, which costs around 40 tokens --
cheap enough to send both cats' maps on every coach evaluation, which is the
point. Apportioning 100 integer units across 25 cells also leaves an average of
4 per cell, so most cells carry real signal instead of rendering a zero.

The cost is shape: a cat confined to a 20x16 patch lands almost entirely in one
cell, so the grid says *which block* it lives in but not the outline of its
wandering. That is survivable only because the two prose lines underneath are
computed in **tile** coordinates and keep full precision regardless of how
coarse the grid is -- NEVER VISITED names exact regions and the confinement line
gives the exact bounding box. The grid is the glance; the prose is the detail.
"""


from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.models.entity import EntityType

# The map is sized in cells per side, not tiles per cell: every conversation
# about this feature has been in terms of "a 5x5 map", and a parameter that
# matches how it is discussed is one nobody has to convert in their head. Tiles
# per cell is derived from it. See module docstring for why the ceiling is low.
DEFAULT_GRID_SIZE = 5
MIN_GRID_SIZE = 2
MAX_GRID_SIZE = 5

# The digits are percentages, so they sum to this.
DIGEST_TOTAL = 100

# Rendered for a cell the cat never stood in. Deliberately distinct from "0",
# which means it was there but for under half a percent of the game -- the
# difference between unexplored ground and ground it merely passed through.
NEVER = "."

Tile = Tuple[int, int]


# -- binning ------------------------------------------------------------------


def cell_size_for_grid(width: int, height: int, grid_size: int) -> int:
    """Tiles per cell needed to fit an arena into `grid_size` cells per side.

    Driven by the longer side so the cells come out **square**. Dividing each
    axis independently would give a 20x10 cell on a 100x50 arena, and a cell
    with twice the area collects roughly twice the ticks for no reason the
    reader can see -- the map would report the arena's shape as the cat's
    behaviour.
    """
    grid_size = max(1, int(grid_size))
    longest = max(1, int(width), int(height))
    return max(1, math.ceil(longest / grid_size))


def grid_dims(width: int, height: int, cell_size: int) -> Tuple[int, int, int]:
    """Cell grid shape for an arena, with `cell_size` clamped to something sane.

    Rounded up, so an arena that does not divide evenly gets a final row and
    column of clipped cells rather than losing its last few tiles.
    """
    cell_size = max(1, int(cell_size))
    cols = max(1, math.ceil(width / cell_size))
    rows = max(1, math.ceil(height / cell_size))
    return rows, cols, cell_size


def bin_positions(
    positions: Sequence[Tile], width: int, height: int, cell_size: int
) -> Tuple[int, int, List[int]]:
    """Counts how many samples fall in each cell, row-major."""
    rows, cols, cell_size = grid_dims(width, height, cell_size)
    counts = [0] * (rows * cols)
    for x, y in positions:
        if not (0 <= x < width and 0 <= y < height):
            continue
        # min() guards the clipped edge cells: a tile at x == width - 1 on a
        # non-divisible arena would otherwise index one column past the end.
        col = min(cols - 1, x // cell_size)
        row = min(rows - 1, y // cell_size)
        counts[row * cols + col] += 1
    return rows, cols, counts


def apportion(counts: Sequence[int], total: int = DIGEST_TOTAL) -> List[int]:
    """Scales counts to integers summing to exactly `total`.

    Largest remainder, in integer arithmetic. Rounding each cell independently
    would land on 97 or 103 and quietly break the one property that makes the
    map readable without a legend, and doing it in floats would make the result
    depend on summation order.

    A cell with no samples never receives a unit: its remainder is zero, so it
    is excluded from the contenders outright. The leftover can never exceed the
    number of cells with a non-zero remainder.
    """
    grand = sum(counts)
    if grand <= 0:
        return [0] * len(counts)

    scaled = [c * total for c in counts]
    values = [s // grand for s in scaled]
    remainders = [s % grand for s in scaled]

    leftover = total - sum(values)
    if leftover > 0:
        contenders = [i for i in range(len(counts)) if remainders[i] > 0]
        # Ties broken by raw count then index, so the same board always renders
        # the same map.
        contenders.sort(key=lambda i: (-remainders[i], -counts[i], i))
        for i in contenders[:leftover]:
            values[i] += 1
    return values


# -- rendering ----------------------------------------------------------------


def render_grid(values: Sequence[int], counts: Sequence[int], rows: int, cols: int) -> List[str]:
    """Draws the cell grid, compass-labelled.

    Column width follows the widest value rather than being fixed at one
    character. A cat that parks 30% of its life in a single cell needs two
    digits, and clamping it to 9 would break the sum.
    """
    width = max(1, max((len(str(v)) for v in values), default=1))

    body_lines: List[str] = []
    for r in range(rows):
        cells = []
        for c in range(cols):
            i = r * cols + c
            text = NEVER if counts[i] == 0 else str(values[i])
            cells.append(text.rjust(width))
        body_lines.append("  ".join(cells))

    inner = len(body_lines[0]) if body_lines else 0
    border = "   +" + "-" * (inner + 2) + "+"
    header = "     " + "W" + " " * max(0, inner - 2) + "E"

    out = [header, border]
    for r, body in enumerate(body_lines):
        label = "N" if r == 0 else ("S" if r == rows - 1 else "")
        out.append(f"{label:>2} | {body} |")
    out.append(border)
    return out


# -- prose summaries ----------------------------------------------------------


def _sector_of(x: int, y: int, width: int, height: int) -> str:
    """Which of the nine compass sectors a tile falls in."""
    col = min(2, (3 * x) // max(1, width))
    row = min(2, (3 * y) // max(1, height))
    return _SECTOR_NAMES[row][col]


# Thirds crossed with thirds. Chosen over the "western third / NW quadrant /
# NW corner" vocabulary that came before because those overlap: an empty north
# and an empty west share the north-west, so both got named and the reader had
# to work out that the two claims were partly the same claim. These nine are
# mutually exclusive and exhaustive, so the list is always the shortest true
# description of where the cat has not been.
_SECTOR_NAMES = (
    ("NW", "N", "NE"),
    ("W", "C", "E"),
    ("SW", "S", "SE"),
)

_SECTOR_ORDER = [name for row in _SECTOR_NAMES for name in row]


def describe_never_visited(visited: Set[Tile], width: int, height: int) -> str:
    """Names the sectors the cat has never entered, across the whole game.

    Takes the uncapped visited set rather than the tick window, because "never"
    is a claim about the whole game and the window cannot support it.
    """
    total = width * height
    unseen = total - len(visited)
    if unseen <= 0:
        return "NEVER VISITED: none - every tile has been stood on"

    # Walked once over the visited tiles rather than once per candidate region
    # over the arena: an arena may be 1000x1000, but the visited set is bounded
    # by the number of ticks played.
    touched = {_sector_of(x, y, width, height) for x, y in visited}
    empty = [name for name in _SECTOR_ORDER if name not in touched]

    pct = 100.0 * unseen / total if total else 0.0
    if len(empty) == len(_SECTOR_ORDER):
        return f"NEVER VISITED: the entire arena ({total:,} tiles) - nothing recorded yet"

    # Clamped below 100 because rounding was reporting a cat that had covered
    # five tiles as having missed "100% of arena", which contradicts the grid
    # sitting directly above it.
    shown = min(99, round(pct))
    head = f"NEVER VISITED: {unseen:,} of {total:,} tiles ({shown}% of arena)"
    if empty:
        head += f" - empty sectors: {', '.join(empty)}"
    return head


def describe_confinement(
    recent: Sequence[Tile], width: int, height: int, ticks: int
) -> Optional[str]:
    """Bounding box of the recent window, as a share of the arena.

    The counterpart to the never-visited line: that one says where the cat has
    not been all game, this one says how little of the arena it is using now.
    """
    if not recent:
        return None
    xs = [x for x, _ in recent]
    ys = [y for _, y in recent]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    area = (x1 - x0 + 1) * (y1 - y0 + 1)
    total = max(1, width * height)
    pct = 100.0 * area / total
    return f"LAST {ticks} TICKS: confined to x=[{x0},{x1}], y=[{y0},{y1}]  ({pct:.0f}% of arena)"


# -- assembly -----------------------------------------------------------------


def _cat_series(sim: Any) -> Dict[str, List[Tile]]:
    """Per-cat position series, oldest first, including the live tick.

    ``_save_snapshot`` runs *before* the step, so the newest snapshot is the
    board as it stood one tick ago and the current positions are not in history
    at all. Appending the live entities keeps the series flush with the tick
    counter the header reports.
    """
    series: Dict[str, List[Tile]] = {}
    for snap in getattr(sim, "history", []):
        for entity in snap.entities:
            if entity.type != EntityType.CAT:
                continue
            series.setdefault(entity.id, []).append(
                (entity.position.x, entity.position.y)
            )
    for entity in sim.entity_manager.entities.values():
        if entity.type != EntityType.CAT:
            continue
        series.setdefault(entity.id, []).append(
            (entity.position.x, entity.position.y)
        )
    return series


def build(
    sim: Any,
    grid_size: int = DEFAULT_GRID_SIZE,
    last_n: Optional[int] = None,
) -> Dict[str, Any]:
    """Builds the movement map payload for every cat in the simulation.

    `grid_size` is cells per side, clamped to the supported range rather than
    rejected: this is a diagnostic view, and refusing to draw a map because
    someone asked for a 40x40 one helps nobody.
    """
    width, height = sim.world.width, sim.world.height
    tick = int(getattr(sim.scheduler, "tick", 0) or 0)

    grid_size = max(MIN_GRID_SIZE, min(MAX_GRID_SIZE, int(grid_size)))
    cell_size = cell_size_for_grid(width, height, grid_size)
    rows, cols, cell_size = grid_dims(width, height, cell_size)

    series = _cat_series(sim)
    # History is capped, so the window can be shorter than the game even when
    # the caller asked for everything. Reported rather than glossed over: a
    # partial window presented as a whole game is the one way this map can lie.
    max_history = int(getattr(sim, "max_history", 0) or 0)
    capped = bool(max_history) and len(getattr(sim, "history", [])) >= max_history

    cats: List[Dict[str, Any]] = []
    for cat_id in sorted(series):
        observed = series[cat_id]
        samples = observed
        if last_n is not None and last_n > 0:
            samples = samples[-last_n:]

        _, _, counts = bin_positions(samples, width, height, cell_size)
        values = apportion(counts)

        # Union, not either source alone. `CatStats.visited` is the uncapped
        # record but it is only written from `record_action`, so a tile the cat
        # is standing on before it has acted is missing from it -- which showed
        # up as a grid reading 100% in a cell directly above a line claiming the
        # cat had never visited anything. Every position in a snapshot is by
        # definition a tile the cat stood on, so folding them in makes the two
        # halves of the report incapable of contradicting each other.
        stats = sim.stats.cats.get(cat_id)
        visited: Set[Tile] = set(observed)
        if stats:
            visited |= set(stats.visited)

        window = len(samples)
        header = (
            f"TIME SPENT - {cat_id} "
            f"(each cell = {cell_size}x{cell_size} tiles; digit = % of ticks, {NEVER} = never)"
        )
        scope = (
            f"window: last {window} ticks of {tick}"
            if window < tick
            else f"window: all {window} ticks"
        )

        lines = [header, scope]
        lines += render_grid(values, counts, rows, cols)
        lines.append(f"TOTAL: {sum(values)}")
        lines.append(describe_never_visited(visited, width, height))
        confinement = describe_confinement(samples, width, height, window)
        if confinement:
            lines.append(confinement)

        cats.append({
            "id": cat_id,
            "mode": sim.entity_manager.get_agent_mode(cat_id),
            "values": [values[r * cols:(r + 1) * cols] for r in range(rows)],
            "counts": [counts[r * cols:(r + 1) * cols] for r in range(rows)],
            "total": sum(values),
            "window_ticks": window,
            "unique_tiles": len(visited),
            "path": [[x, y] for x, y in samples],
            "ascii": "\n".join(lines),
        })

    return {
        "simulation_id": sim.simulation_id,
        "tick": tick,
        "arena": {"width": width, "height": height},
        # Both reported: the UI slider is in grid_size, the legend is in
        # cell_size, and the clamp means the requested value may not be the one
        # that was drawn.
        "grid_size": grid_size,
        "cell_size": cell_size,
        "rows": rows,
        "cols": cols,
        "window_capped": capped,
        "cats": cats,
        "ascii": "\n\n".join(c["ascii"] for c in cats),
    }
