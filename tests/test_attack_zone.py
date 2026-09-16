"""The cat's strike zone is a rectangle, and three implementations must agree.

The zone used to be a ~60-70 degree cone whose lateral spread grew as
``round(dist * tan(35deg))`` and whose outer edge was clipped by
``hypot(dist, lat) <= range + 0.5``. At the shipped default range of 8 that was
a 50-tile blob reaching 4 tiles off-axis, with membership decided by
floating-point rounding.

The actor could not predict it. It attacked visible-but-unreachable mice for
entire games, and the evaluator wrote three separate coaching notes trying to
reverse-engineer the boundary, landing on a different wrong rule per facing.

There are three independent implementations of this shape:

  1. ``World.get_slash_aoe_tiles``      -- the engine; decides who dies
  2. ``getSlashAoeTiles`` in app.js     -- the canvas; decides what you see
  3. ``heuristic.in_strike_zone``       -- the reflex; decides when to swing

If any two disagree, the game lies to somebody. These tests compare all three
against each other rather than against a restatement of the rule.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from actors.cat.heuristic import decompose_offset, in_strike_zone
from app.game.world import World
from app.models.entity import Direction
from app.models.simulation import SimulationConfig, Topology

REPO = Path(__file__).resolve().parents[1]
APP_JS = REPO / "app" / "static" / "app.js"
SKILLS_MD = REPO / "actors" / "cat" / "actor_skills.md"
JSC = Path(
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
)

FACINGS = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]


# --------------------------------------------------------------------------
# 1. The engine produces the intended shape
# --------------------------------------------------------------------------

def test_default_zone_is_exactly_the_six_tiles_facing_east():
    """The shape this change was specified with, spelled out tile by tile."""
    w = World(width=50, height=50, topology=Topology.BOUNDED)
    tiles = set(w.get_slash_aoe_tiles(_pos(10, 10), Direction.EAST, range_tiles=2))
    assert tiles == {
        (11, 9), (12, 9),
        (11, 10), (12, 10),
        (11, 11), (12, 11),
    }


@pytest.mark.parametrize("facing,expected", [
    (Direction.NORTH, {(9, 8), (10, 8), (11, 8), (9, 9), (10, 9), (11, 9)}),
    (Direction.SOUTH, {(9, 11), (10, 11), (11, 11), (9, 12), (10, 12), (11, 12)}),
    (Direction.EAST, {(11, 9), (11, 10), (11, 11), (12, 9), (12, 10), (12, 11)}),
    (Direction.WEST, {(9, 9), (9, 10), (9, 11), (8, 9), (8, 10), (8, 11)}),
])
def test_zone_is_six_tiles_for_every_facing(facing, expected):
    w = World(width=50, height=50, topology=Topology.BOUNDED)
    assert set(w.get_slash_aoe_tiles(_pos(10, 10), facing, range_tiles=2)) == expected


@pytest.mark.parametrize("rng", [1, 2, 3, 5, 8])
@pytest.mark.parametrize("facing", FACINGS)
def test_zone_is_always_three_tiles_wide(rng, facing):
    """The old cone widened with distance; a rectangle must not."""
    w = World(width=200, height=200, topology=Topology.BOUNDED)
    tiles = w.get_slash_aoe_tiles(_pos(100, 100), facing, range_tiles=rng)
    assert len(tiles) == rng * 3, f"expected {rng * 3} tiles, got {len(tiles)}"

    for tx, ty in tiles:
        forward, lateral = decompose_offset(tx - 100, ty - 100, facing.value)
        assert 1 <= forward <= rng, f"tile at forward={forward} outside depth {rng}"
        assert abs(lateral) <= 1, (
            f"tile {lateral} off-axis; the zone widened again"
        )


def test_zone_never_reaches_behind_or_sideways():
    w = World(width=50, height=50, topology=Topology.BOUNDED)
    tiles = set(w.get_slash_aoe_tiles(_pos(25, 25), Direction.EAST, range_tiles=4))
    assert (25, 25) not in tiles, "the cat's own tile is in its strike zone"
    assert not any(tx <= 25 for tx, _ in tiles), "the zone extends behind the cat"


# --------------------------------------------------------------------------
# 2. The reflex agrees with the engine
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rng", [1, 2, 3, 8])
@pytest.mark.parametrize("facing", FACINGS)
def test_reflex_membership_matches_the_engine_exactly(rng, facing):
    """Every tile in a 21x21 neighbourhood, checked both ways.

    The reflex previously used ``abs(dx) + abs(dy) <= atk_range``. Manhattan
    distance is not a rectangle: at range 2 it excludes the tile 2 ahead and 1
    off-axis, which the engine hits. That tile is a corner of the zone, i.e.
    exactly where a fleeing mouse tends to be.
    """
    w = World(width=200, height=200, topology=Topology.BOUNDED)
    cx = cy = 100
    engine = set(w.get_slash_aoe_tiles(_pos(cx, cy), facing, range_tiles=rng))

    for dx in range(-10, 11):
        for dy in range(-10, 11):
            reflex_says = in_strike_zone(dx, dy, facing.value, rng)
            engine_says = (cx + dx, cy + dy) in engine
            assert reflex_says == engine_says, (
                f"disagreement at offset ({dx},{dy}) facing {facing.value} "
                f"range {rng}: reflex={reflex_says} engine={engine_says}"
            )


def test_the_corner_tile_manhattan_used_to_miss():
    """Regression: forward=2, offaxis=1 sums to 3, which exceeds range 2."""
    assert in_strike_zone(2, 1, "east", 2) is True
    assert in_strike_zone(2, -1, "east", 2) is True


def test_the_attack_from_the_coaching_note_is_out_of_range():
    """From the evaluator's own notes: attacked (97,76) from (89,72).

    dx=8, dy=4. Under the old cone this was excluded only because
    hypot(8,4)=8.94 exceeded 8.5 -- a boundary no prompt could convey. Under
    the rectangle it is out because 4 > 1, which is stateable in one clause.
    """
    assert in_strike_zone(8, 4, "east", 8) is False
    _, lateral = decompose_offset(8, 4, "east")
    assert abs(lateral) == 4


# --------------------------------------------------------------------------
# 2b. Both decision functions agree with the engine
# --------------------------------------------------------------------------
#
# in_strike_zone being correct is not enough -- a decision function only
# benefits if it actually calls it. compute_heuristic_decision kept its own
# inline `abs(dx) + abs(dy) <= atk_range` copy after the reflex was migrated,
# and silently declined a third of the zone. These tests drive the public
# entrypoints rather than the predicate, so an unmigrated caller fails here.

def _obs_with_mouse_at(dx, dy, facing, atk_range):
    from actors.models import Observation

    cx = cy = 100
    return Observation(
        tick=1,
        round=0,
        self={"id": "cat-1", "position": {"x": cx, "y": cy}, "facing": facing},
        visible_entities=[
            {"id": "mouse-1", "type": "mouse", "position": {"x": cx + dx, "y": cy + dy}}
        ],
        # "lunge" is deliberately withheld: it is a lower rung, but including
        # it would let a lunge mask a declined attack and turn a real miss
        # into a passing test.
        legal_actions=["north", "south", "east", "west", "stay", "attack"],
        capabilities={"attack_range": atk_range, "vision_radius": 20},
    )


@pytest.mark.parametrize(
    "decide_name", ["compute_heuristic_decision", "compute_reflex_decision"]
)
@pytest.mark.parametrize("rng", [1, 2, 3, 8])
@pytest.mark.parametrize("facing", FACINGS)
def test_both_decision_paths_swing_exactly_when_the_engine_would_hit(
    decide_name, rng, facing
):
    import actors.cat.heuristic as heuristic

    decide = getattr(heuristic, decide_name)
    w = World(width=200, height=200, topology=Topology.BOUNDED)
    cx = cy = 100
    engine = set(w.get_slash_aoe_tiles(_pos(cx, cy), facing, range_tiles=rng))

    declined = []
    overswung = []
    for dx in range(-10, 11):
        for dy in range(-10, 11):
            obs = _obs_with_mouse_at(dx, dy, facing.value, rng)
            action, _, _ = decide(obs)
            engine_hits = (cx + dx, cy + dy) in engine
            if engine_hits and action != "attack":
                declined.append((dx, dy))
            if action == "attack" and not engine_hits:
                overswung.append((dx, dy))

    assert not declined, (
        f"{decide_name} facing {facing.value} range {rng} refused free kills at "
        f"{declined} -- the engine would have hit those tiles"
    )
    assert not overswung, (
        f"{decide_name} facing {facing.value} range {rng} swung at empty air at "
        f"{overswung} -- the engine hits nothing there"
    )


def test_both_decision_paths_take_the_corner_manhattan_used_to_decline():
    """The specific tile the two implementations disagreed on.

    forward=2, lateral=1 sums to 3 under Manhattan, exceeding range 2, so the
    old inline predicate in compute_heuristic_decision passed on it. The
    engine hits it.
    """
    import actors.cat.heuristic as heuristic

    for decide in (heuristic.compute_heuristic_decision, heuristic.compute_reflex_decision):
        obs = _obs_with_mouse_at(2, 1, "east", 2)
        action, _, meta = decide(obs)
        assert action == "attack", f"{decide.__name__} declined the corner tile"
        assert meta["target_id"] == "mouse-1"


# --------------------------------------------------------------------------
# 3. The canvas agrees with the engine
# --------------------------------------------------------------------------

def _extract_js_function(source: str, name: str) -> str:
    """Pull a function out of app.js by brace matching. node is not installed."""
    start = source.index(f"function {name}(")
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


@pytest.mark.skipif(not JSC.exists(), reason="JavaScriptCore helper not present")
def test_canvas_and_engine_agree_tile_for_tile():
    """The JS is a hand-copy of the Python. Compare them, do not trust them."""
    src = APP_JS.read_text(encoding="utf-8")
    fn = _extract_js_function(src, "getSlashAoeTiles")

    half_width = re.search(r"const SLASH_HALF_WIDTH = (\d+);", src)
    assert half_width, "SLASH_HALF_WIDTH not found in app.js"

    cases = [
        (100, 100, f, r)
        for f in ("north", "south", "east", "west")
        for r in (1, 2, 3, 8)
    ]

    harness = f"""
    const SLASH_HALF_WIDTH = {half_width.group(1)};
    {fn}
    const cases = {json.dumps(cases)};
    const out = cases.map(([x, y, facing, rng]) =>
      getSlashAoeTiles({{x, y}}, facing, rng, 200, 200, 'bounded')
        .map(t => [t.x, t.y])
        .sort((a, b) => a[0] - b[0] || a[1] - b[1])
    );
    print(JSON.stringify(out));
    """
    result = subprocess.run(
        [str(JSC), "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, f"jsc failed: {result.stderr}"
    js_out = json.loads(result.stdout.strip())

    w = World(width=200, height=200, topology=Topology.BOUNDED)
    for (x, y, facing, rng), js_tiles in zip(cases, js_out):
        py_tiles = sorted(
            w.get_slash_aoe_tiles(_pos(x, y), Direction(facing), range_tiles=rng)
        )
        assert [tuple(t) for t in js_tiles] == py_tiles, (
            f"canvas and engine disagree for facing={facing} range={rng}:\n"
            f"  canvas: {js_tiles}\n  engine: {py_tiles}"
        )


# --------------------------------------------------------------------------
# 4. Config and documentation
# --------------------------------------------------------------------------

def test_cat_default_attack_range_gives_the_twelve_tile_zone():
    """Depth 4 x the fixed 3-tile width = the 12 tiles ahead of the cat.

    Raised from 2 to offset the cat being slower than the mouse: it cannot win
    a straight chase, so reach is what makes the pursuit end in a kill.
    """
    cfg = SimulationConfig()
    assert cfg.species["cat"].attack_range == 4, (
        "the default depth no longer produces the 12-tile zone"
    )


def test_vision_still_outranges_attack():
    """The gap between what you see and what you can hit is the whole game."""
    cfg = SimulationConfig()
    cat = cfg.species["cat"]
    assert cat.vision_radius > cat.attack_range


def test_manual_describes_a_rectangle_not_a_cone():
    manual = SKILLS_MD.read_text()
    attack_section = manual[manual.index("### Skill: `attack`"):]
    attack_section = attack_section[:attack_section.index("---")]

    assert "60°" not in attack_section, (
        "the manual still calls the strike zone a 60 degree cone"
    )
    assert "rectangle" in attack_section.lower()
    assert "offaxis" in attack_section, "no off-axis test is given"


def test_manual_gives_the_integer_membership_test():
    """Prose alone left the model estimating. It needs the comparison."""
    manual = SKILLS_MD.read_text()
    assert "1 <= forward <= attack_range" in manual
    assert "-1 <= offaxis <= 1" in manual


def test_manual_warns_that_visible_is_not_attackable():
    """The actual failure: it swung at every mouse it could see."""
    manual = SKILLS_MD.read_text().lower()
    assert "most mice you can\nsee, you cannot hit" in manual or (
        "most mice you can see, you cannot hit" in manual
    ), "the manual never separates visibility from reachability"


def test_prompt_no_longer_calls_the_zone_a_cone():
    from actors.cat.actor import build_decision_prompt
    from actors.models import Observation

    obs = Observation(
        tick=1, round=1, agent_id="cat-1",
        self={"id": "cat-1", "position": {"x": 5, "y": 5},
              "facing": "east", "type": "cat"},
        visible_entities=[], legal_actions=["stay", "attack"],
        capabilities={"attack_range": 2, "vision_radius": 10,
                      "move_cooldown": 3, "attack_cooldown": 3, "lunge_cooldown": 2},
        score=0, scoreboard={},
    )
    prompt = build_decision_prompt(obs, history=[])
    assert "60°" not in prompt
    assert "3 tiles wide" in prompt


# --------------------------------------------------------------------------

def _pos(x: int, y: int):
    from app.models.entity import Position
    return Position(x=x, y=y)
