"""Tests for the downsampled movement map.

The invariant under most scrutiny here is that the digits sum to exactly 100.
It is what lets the map be read as a distribution without a legend, and it is
the kind of property that a plausible-looking rounding change breaks silently --
the map still renders, it just quietly stops adding up.
"""

import random

import pytest

from app.game import heatmap
from app.game.heatmap import (
    apportion,
    bin_positions,
    cell_size_for_grid,
    describe_confinement,
    describe_never_visited,
    grid_dims,
    render_grid,
)


# -- apportionment ------------------------------------------------------------


def test_apportion_sums_to_exactly_100():
    counts = [3, 7, 11, 0, 1, 29, 4]
    assert sum(apportion(counts)) == 100


def test_apportion_sums_to_100_across_random_distributions():
    """The invariant has to hold for shapes nobody thought to hand-write."""
    rng = random.Random(1234)
    for _ in range(300):
        size = rng.randint(1, 625)
        counts = [rng.choice([0, 0, 0, 1, rng.randint(1, 500)]) for _ in range(size)]
        if sum(counts) == 0:
            continue
        assert sum(apportion(counts)) == 100, counts[:20]


def test_apportion_empty_history_is_all_zero():
    """A game with no ticks reports nothing, rather than inventing a total."""
    assert apportion([0, 0, 0]) == [0, 0, 0]
    assert apportion([]) == []


def test_apportion_never_credits_an_unvisited_cell():
    """A cell with no samples must stay 0, or `.` would contradict its digit.

    Largest remainder hands leftover units to the biggest fractional parts, and
    a zero count has no fractional part. If this ever regressed, the map would
    show a percentage for ground the cat never touched.
    """
    counts = [1] * 3 + [0] * 97
    values = apportion(counts)
    assert sum(values) == 100
    for count, value in zip(counts, values):
        if count == 0:
            assert value == 0


def test_apportion_matches_hand_worked_largest_remainder():
    # 3 cells, 3 ticks each: exact share is 33.33 each, floors to 33 and leaves
    # 1 unit over. Ties break by index, so the first cell takes it.
    assert apportion([1, 1, 1]) == [34, 33, 33]


def test_apportion_concentration_exceeds_one_digit():
    """A camping cat produces values above 9, which must not be clamped."""
    counts = [900] + [10] * 10
    values = apportion(counts)
    assert sum(values) == 100
    assert values[0] > 9


# -- binning ------------------------------------------------------------------


def test_cell_size_for_grid_fits_the_arena():
    assert cell_size_for_grid(100, 100, 5) == 20
    assert cell_size_for_grid(100, 100, 2) == 50


def test_cell_size_for_grid_keeps_cells_square_on_a_non_square_arena():
    """Unequal cell areas would report the arena's shape as the cat's behaviour."""
    cell = cell_size_for_grid(100, 50, 5)
    assert cell == 20
    rows, cols, _ = grid_dims(100, 50, cell)
    # Square cells, so the short axis simply gets fewer rows.
    assert cols == 5
    assert rows == 3


def test_grid_dims_rounds_up_for_non_divisible_arenas():
    rows, cols, cell = grid_dims(100, 100, 10)
    assert (rows, cols, cell) == (10, 10, 10)
    # 100 / 30 -> 4 columns, the last one clipped
    rows, cols, _ = grid_dims(100, 100, 30)
    assert (rows, cols) == (4, 4)


def test_bin_positions_counts_into_the_right_cell():
    rows, cols, counts = bin_positions([(0, 0), (0, 0), (99, 99)], 100, 100, 10)
    assert (rows, cols) == (10, 10)
    assert counts[0] == 2
    assert counts[-1] == 1
    assert sum(counts) == 3


def test_bin_positions_keeps_edge_tiles_inside_the_grid():
    """A clipped edge cell must absorb its tiles rather than index past the end."""
    _, cols, counts = bin_positions([(99, 99)], 100, 100, 30)
    assert sum(counts) == 1
    assert counts[-1] == 1


def test_bin_positions_ignores_out_of_bounds_samples():
    _, _, counts = bin_positions([(-1, 0), (0, 500)], 100, 100, 10)
    assert sum(counts) == 0


@pytest.mark.parametrize("cell_size", [1, 3, 7, 10, 100, 250])
def test_sum_invariant_holds_for_any_cell_size(cell_size):
    rng = random.Random(7)
    positions = [(rng.randrange(100), rng.randrange(100)) for _ in range(400)]
    _, _, counts = bin_positions(positions, 100, 100, cell_size)
    assert sum(apportion(counts)) == 100


# -- rendering ----------------------------------------------------------------


def test_render_distinguishes_never_from_rounds_to_zero():
    """`.` and `0` mean different things and must not collapse together.

    One cell holds every tick but one; the other holds the single remaining
    tick, which rounds to 0%. The second cell was visited and has to say so.
    """
    counts = [9999, 1, 0, 0]
    values = apportion(counts)
    assert values[1] == 0
    lines = render_grid(values, counts, rows=2, cols=2)
    body = "\n".join(lines)
    assert "0" in body
    assert "." in body


def test_render_widens_columns_for_two_digit_values():
    counts = [50, 50]
    values = apportion(counts)
    lines = render_grid(values, counts, rows=1, cols=2)
    assert "50" in "\n".join(lines)


def test_render_is_rectangular():
    """Ragged rows would misalign the compass and misread as a different shape."""
    counts = [1, 2, 3, 4, 5, 6]
    values = apportion(counts)
    lines = render_grid(values, counts, rows=2, cols=3)
    grid_lines = [ln for ln in lines if ln.strip().endswith("|") and "|" in ln]
    assert len({len(ln) for ln in grid_lines}) == 1


# -- prose --------------------------------------------------------------------


def test_never_visited_names_the_empty_sectors():
    # Everything east of the western third has been walked.
    visited = {(x, y) for x in range(34, 100) for y in range(100)}
    line = describe_never_visited(visited, 100, 100)
    assert "empty sectors: NW, W, SW" in line
    assert "3,400" in line


def test_never_visited_sectors_do_not_overlap():
    """Nine exclusive sectors, so no area is ever described twice.

    The previous vocabulary mixed thirds, quadrants and corners, which overlap:
    an empty north and an empty west both claimed the north-west, and an unplayed
    game listed eight regions describing the same emptiness.
    """
    line = describe_never_visited(set(), 100, 100)
    assert "the entire arena" in line
    assert line.count("NW") == 0


def test_never_visited_reports_full_coverage():
    visited = {(x, y) for x in range(10) for y in range(10)}
    assert "none" in describe_never_visited(visited, 10, 10)



def test_confinement_reports_the_bounding_box():
    recent = [(52, 9), (71, 24), (60, 15)]
    line = describe_confinement(recent, 100, 100, ticks=300)
    assert "x=[52,71]" in line
    assert "y=[9,24]" in line
    assert "LAST 300 TICKS" in line


def test_confinement_is_omitted_without_samples():
    assert describe_confinement([], 100, 100, ticks=0) is None


# -- end to end ---------------------------------------------------------------


class _FakePosition:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _FakeEntity:
    def __init__(self, entity_id, x, y):
        from app.models.entity import EntityType

        self.id = entity_id
        self.type = EntityType.CAT
        self.position = _FakePosition(x, y)


class _FakeSnapshot:
    def __init__(self, entities):
        self.entities = entities


class _FakeSim:
    """Minimal stand-in exposing only what `heatmap.build` reads."""

    def __init__(self, path, width=100, height=100, max_history=500):
        from app.game.world import World
        from app.game.stats import StatsTracker

        self.simulation_id = "sim-test"
        self.world = World(width=width, height=height)
        self.max_history = max_history
        self.history = [_FakeSnapshot([_FakeEntity("cat-1", x, y)]) for x, y in path[:-1]]
        self.scheduler = type("S", (), {"tick": len(path)})()
        self.stats = StatsTracker(self.world)
        stats = self.stats.get("cat-1")
        for x, y in path:
            stats.visit(x, y)
        last_x, last_y = path[-1]
        self.entity_manager = type(
            "EM",
            (),
            {
                "entities": {"cat-1": _FakeEntity("cat-1", last_x, last_y)},
                "get_agent_mode": staticmethod(lambda _cid: "ws"),
            },
        )()


def test_build_produces_a_grid_summing_to_100():
    path = [(50, 50)] * 40 + [(10, 10)] * 60
    payload = heatmap.build(_FakeSim(path))

    assert payload["grid_size"] == 5
    assert payload["rows"] == 5 and payload["cols"] == 5
    cat = payload["cats"][0]
    assert cat["id"] == "cat-1"
    assert cat["total"] == 100
    assert sum(sum(row) for row in cat["values"]) == 100
    assert "TOTAL: 100" in cat["ascii"]


def test_build_includes_the_live_tick_not_just_history():
    """The newest snapshot predates the current tick, so live must be appended."""
    path = [(0, 0)] * 9 + [(99, 99)]
    payload = heatmap.build(_FakeSim(path))
    cat = payload["cats"][0]
    assert cat["window_ticks"] == 10
    assert cat["values"][-1][-1] > 0


def test_build_last_n_restricts_the_window():
    path = [(0, 0)] * 50 + [(99, 99)] * 50
    payload = heatmap.build(_FakeSim(path), last_n=50)
    cat = payload["cats"][0]
    assert cat["window_ticks"] == 50
    # The window covers only the south-east corner, so it takes everything.
    assert cat["values"][-1][-1] == 100
    assert cat["values"][0][0] == 0


def test_build_states_a_partial_window_rather_than_implying_a_whole_game():
    path = [(5, 5)] * 100
    payload = heatmap.build(_FakeSim(path), last_n=20)
    assert "last 20 ticks of 100" in payload["cats"][0]["ascii"]


def test_build_survives_a_game_with_no_ticks():
    payload = heatmap.build(_FakeSim([(0, 0)]))
    cat = payload["cats"][0]
    assert cat["total"] == 100  # the single live sample is the whole distribution
    assert "TIME SPENT" in cat["ascii"]


@pytest.mark.parametrize("grid_size", [2, 3, 4, 5])
def test_build_honours_every_supported_grid_size(grid_size):
    path = [(x % 100, (x * 7) % 100) for x in range(200)]
    payload = heatmap.build(_FakeSim(path), grid_size=grid_size)
    assert payload["grid_size"] == grid_size
    assert payload["rows"] == grid_size and payload["cols"] == grid_size
    assert payload["cats"][0]["total"] == 100


def test_build_clamps_an_out_of_range_grid_rather_than_failing():
    """A diagnostic view should still draw something when asked for nonsense."""
    payload = heatmap.build(_FakeSim([(0, 0)] * 10), grid_size=40)
    assert payload["grid_size"] == heatmap.MAX_GRID_SIZE
    payload = heatmap.build(_FakeSim([(0, 0)] * 10), grid_size=0)
    assert payload["grid_size"] == heatmap.MIN_GRID_SIZE


def test_build_is_cheap_enough_for_a_prompt():
    """The whole point of 5x5 is the token bill. Guard it."""
    path = [(x % 100, (x * 3) % 100) for x in range(500)]
    payload = heatmap.build(_FakeSim(path))
    assert len(payload["cats"][0]["ascii"]) < 900


def test_build_grid_and_prose_cannot_contradict_each_other():
    """A cell showing 100% must never sit above "never visited anything".

    `CatStats.visited` is only written from `record_action`, so on a board that
    has not resolved an action yet it is empty while the snapshots already know
    exactly where the cat is standing. Reading the two independently produced a
    report that disagreed with itself in adjacent lines.
    """
    sim = _FakeSim([(84, 58)])
    sim.stats.cats.clear()  # nothing recorded yet, as at tick 1

    payload = heatmap.build(sim)
    text = payload["cats"][0]["ascii"]

    assert "100" in text
    assert "the entire arena" not in text
    assert "10,000 of 10,000" not in text


def test_build_includes_recent_25_ticks_confinement_alongside_long_window():
    """A 500-tick rolling window must also report the recent 25-tick bounding box so coaches do not misread old movement as current deviation."""
    path = [(44, 42)] * 50 + [(99, 80 + (i % 15)) for i in range(50)]
    payload = heatmap.build(_FakeSim(path))
    cat = payload["cats"][0]
    text = cat["ascii"]
    assert "LAST 100 TICKS: confined to x=[44,99]" in text
    assert "RECENT 25 TICKS: confined to x=[99,99]" in text
    assert cat["recent_bounds"]["x"] == [99, 99]
