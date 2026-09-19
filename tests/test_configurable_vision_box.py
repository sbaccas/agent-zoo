"""The cat's viewport is three independent, live-tunable numbers.

Before this, `vision_radius` was a per-species config field while the other two
dimensions of the same box were hardcoded class constants on `World`. Half the
shape was operator-controlled and half was not, which meant the settings tab
could not express the box it was nominally configuring.

These tests pin the three properties that make the dial trustworthy:

  1. all three dimensions actually reshape what the engine considers visible,
  2. the numbers survive the round trip from PATCH to observation payload, and
  3. every downstream description of the box -- the actor's prompt, the
     evaluator's derived predicate, the canvas overlay -- is computed from the
     live values rather than restating the shipped ones.

(3) is the one worth the most vigilance. A dial that silently desynchronises
the prompt from the engine is worse than no dial, because the actor's entire
membership test is "compare two integers to the two bounds I was given".
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.game.world import World
from app.main import app
from app.models.entity import Direction, Position
from app.models.simulation import SimulationConfig, SpeciesConfig, Topology, UpdateSpeciesConfigRequest

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# 1. The geometry responds to all three dimensions
# --------------------------------------------------------------------------

def _frame(world, pos, facing, **kw):
    """Tiles re-expressed as (forward, offaxis) in the entity's own frame."""
    deltas = {
        Direction.NORTH: (0, -1),
        Direction.SOUTH: (0, 1),
        Direction.EAST: (1, 0),
        Direction.WEST: (-1, 0),
    }
    dx, dy = deltas[facing]
    px, py = -dy, dx
    out = set()
    for tx, ty in world.get_fov_tiles(pos, facing, **kw):
        ox, oy = tx - pos.x, ty - pos.y
        out.add((ox * dx + oy * dy, ox * px + oy * py))
    return out


@pytest.fixture
def world():
    return World(width=200, height=200, topology=Topology.BOUNDED)


@pytest.fixture
def centre():
    return Position(x=100, y=100)


def test_the_defaults_reproduce_the_shipped_box(world, centre):
    """No arguments must still mean the documented 13x7."""
    tiles = world.get_fov_tiles(centre, Direction.EAST, distance=10)
    assert len(tiles) == 13 * 7 == 91


@pytest.mark.parametrize("distance,rear,half", [
    (10, 2, 3),    # shipped
    (4, 0, 0),     # single-file lane straight ahead
    (1, 10, 10),   # near-blind forward, huge peripheral
    (6, 5, 8),
    (20, 1, 1),
])
def test_every_dimension_is_honoured_independently(world, centre, distance, rear, half):
    """The box is exactly the cross product of the three bounds, nothing else."""
    expected = {
        (f, l)
        for f in range(-rear, distance + 1)
        for l in range(-half, half + 1)
    }
    got = _frame(world, centre, Direction.EAST, distance=distance, rear=rear, half_width=half)
    assert got == expected
    assert len(got) == (distance + rear + 1) * (2 * half + 1)


@pytest.mark.parametrize("facing", list(Direction))
def test_tuned_boxes_stay_correct_in_every_facing(world, centre, facing):
    """The perpendicular vector flips sign with facing.

    A mirrored or rotated box would be invisible at the shipped symmetric
    values, so this is checked at deliberately asymmetric ones.
    """
    got = _frame(world, centre, facing, distance=7, rear=3, half_width=5)
    assert got == {(f, l) for f in range(-3, 8) for l in range(-5, 6)}


def test_a_zero_half_width_is_a_single_file_lane(world, centre):
    """A legal, if brutal, setting. It must not collapse to an empty set."""
    tiles = _frame(world, centre, Direction.EAST, distance=5, rear=0, half_width=0)
    assert tiles == {(f, 0) for f in range(0, 6)}
    assert (0, 0) in tiles, "the entity must still occupy its own tile"


def test_negative_dimensions_are_clamped_not_raised(world, centre):
    """These arrive from a live PATCH that can land mid-tick.

    Pydantic rejects negatives at the API edge, so reaching here means a
    direct caller. Perception is the wrong place to take down the simulation,
    so the contract is "clamp to zero", not "raise".
    """
    tiles = _frame(world, centre, Direction.EAST, distance=3, rear=-5, half_width=-2)
    assert tiles == {(f, 0) for f in range(0, 4)}


def test_tuning_the_box_changes_what_is_actually_visible(world):
    """The point of the whole exercise, stated as a visibility claim."""
    cat = Position(x=100, y=100)
    mouse = (105, 95)  # 5 ahead, 5 off-axis, facing east

    narrow = world.get_fov_tiles(cat, Direction.EAST, distance=10, rear=2, half_width=3)
    wide = world.get_fov_tiles(cat, Direction.EAST, distance=10, rear=2, half_width=8)

    assert mouse not in set(narrow), "off-axis 5 must be outside a half-width of 3"
    assert mouse in set(wide), "off-axis 5 must be inside a half-width of 8"


# --------------------------------------------------------------------------
# 2. The config carries them, and the API round-trips them
# --------------------------------------------------------------------------

def test_the_shipped_cat_box_is_stated_explicitly_in_the_species_table():
    """Regression: these were class constants and invisible to the config."""
    cat = SimulationConfig().species["cat"]
    assert (cat.vision_radius, cat.vision_rear, cat.vision_half_width) == (10, 2, 3)


def test_the_species_defaults_match_the_world_fallbacks():
    """Two sources of truth exist; they must at least agree at the default.

    `World.FOV_*` now only applies when a caller supplies nothing. If it ever
    disagreed with SpeciesConfig, behaviour would depend on the call path.
    """
    spec = SpeciesConfig()
    assert spec.vision_rear == World.FOV_REAR_DEPTH
    assert spec.vision_half_width == World.FOV_HALF_WIDTH


@pytest.mark.parametrize("field,value", [
    ("vision_rear", -1),
    ("vision_half_width", -1),
    ("vision_rear", 101),
    ("vision_half_width", 101),
])
def test_out_of_range_dimensions_are_rejected_at_the_edge(field, value):
    with pytest.raises(Exception):
        UpdateSpeciesConfigRequest(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("vision_rear", 0),
    ("vision_half_width", 0),
    ("vision_rear", 100),
    ("vision_half_width", 100),
])
def test_the_extremes_of_the_range_are_accepted(field, value):
    """0 is meaningful (blind behind / single-file) and must not be rejected."""
    assert getattr(UpdateSpeciesConfigRequest(**{field: value}), field) == value


@pytest.fixture
def client_sim():
    c = TestClient(app)
    sid = c.post("/api/simulations", json={
        "width": 60, "height": 60, "populations": {"cats": 1, "mice": 3},
    }).json()["simulation_id"]
    return c, sid


def test_a_patch_reshapes_the_live_observation(client_sim):
    """End to end: settings tab -> engine -> the payload the agent reads."""
    c, sid = client_sim

    before = c.get(f"/api/simulations/{sid}/agents/cat-1/observation").json()["capabilities"]
    assert (before["vision_radius"], before["vision_rear"], before["vision_half_width"]) == (10, 2, 3)

    r = c.patch(f"/api/simulations/{sid}/species/cat",
                json={"vision_radius": 6, "vision_rear": 5, "vision_half_width": 8})
    assert r.status_code == 200

    after = c.get(f"/api/simulations/{sid}/agents/cat-1/observation").json()["capabilities"]
    assert (after["vision_radius"], after["vision_rear"], after["vision_half_width"]) == (6, 5, 8)


def test_a_rejected_patch_leaves_the_box_untouched(client_sim):
    """A 422 must not partially apply."""
    c, sid = client_sim
    r = c.patch(f"/api/simulations/{sid}/species/cat",
                json={"vision_radius": 7, "vision_half_width": -4})
    assert r.status_code == 422

    caps = c.get(f"/api/simulations/{sid}/agents/cat-1/observation").json()["capabilities"]
    assert (caps["vision_radius"], caps["vision_half_width"]) == (10, 3)


def test_the_overlay_is_told_the_box(client_sim):
    """The canvas reads state.species; without these keys it would guess.

    Pinned against `/state` specifically because that is the endpoint app.js
    polls on every frame to draw the red viewport rectangle.
    """
    c, sid = client_sim
    cat = c.get(f"/api/simulations/{sid}/state").json()["species"]["cat"]
    assert "vision_rear" in cat and "vision_half_width" in cat


# --------------------------------------------------------------------------
# 3. Nothing downstream restates the numbers
# --------------------------------------------------------------------------

def test_the_actor_prompt_derives_the_box_from_capabilities():
    """A prompt that says "3 either side" while the engine runs 8 is a lie.

    The actor's whole membership test is a comparison against these bounds, so
    a stale prompt does not degrade play gracefully -- it makes the rule wrong.
    """
    import actors.cat.actor as actor

    src = Path(actor.__file__).read_text()
    assert "plus 2 behind and 3 either side" not in src, (
        "the prompt is restating the shipped box instead of reading capabilities"
    )
    assert 'caps.get("vision_rear"' in src or "caps.get('vision_rear'" in src
    assert 'caps.get("vision_half_width"' in src or "caps.get('vision_half_width'" in src


@pytest.mark.parametrize("fwd,rear,half", [(10, 2, 3), (6, 0, 0), (4, 5, 8)])
def test_the_evaluator_predicate_is_derived_from_the_live_box(fwd, rear, half):
    """Same contract the strike-zone predicate already has."""
    import actors.cat.subagents.eval as ev

    records = [{
        "tick": i,
        "capabilities": {
            "attack_range": 4,
            "vision_radius": fwd,
            "vision_rear": rear,
            "vision_half_width": half,
            "move_cooldown": 3,
        },
    } for i in range(3)]

    block = ev.format_capabilities(records)
    assert f"{-rear} <= forward <= {fwd}" in block
    assert f"{-half} <= offaxis <= {half}" in block
    assert f"{fwd + rear + 1} deep and {2 * half + 1} wide" in block


def test_the_evaluator_is_told_not_to_fault_the_cat_for_the_unseeable():
    """The box is wider than the strike zone; that gap is not a failure.

    This is the note that stops the coach reading "saw it, did not swing" off
    every tick where a mouse was visible but out of reach.
    """
    import actors.cat.subagents.eval as ev

    block = ev.format_capabilities([{
        "tick": 1,
        "capabilities": {"attack_range": 4, "vision_radius": 10,
                         "vision_rear": 2, "vision_half_width": 3},
    }])
    assert "could not see" in block
    assert "wider than the strike zone" in block


def test_the_evaluator_block_survives_transcripts_that_predate_the_fields():
    """Old records have vision_radius but not the other two. Emit no predicate
    rather than inventing bounds."""
    import actors.cat.subagents.eval as ev

    block = ev.format_capabilities([{
        "tick": 1,
        "capabilities": {"attack_range": 4, "vision_radius": 10},
    }])
    assert "Strike zone implied" in block, "the strike zone must still render"
    assert "Viewport implied" not in block


def test_the_overlay_does_not_hardcode_the_box():
    """The canvas must not keep a private copy of a live-tunable number."""
    js = (REPO / "app/static/app.js").read_text()
    assert "const HALF_WIDTH = state.species?.cat?.vision_half_width" in js
    assert "const REAR_DEPTH = state.species?.cat?.vision_rear" in js


@pytest.mark.parametrize("page", ["index.html", "settings.html"])
def test_both_settings_surfaces_expose_all_three_dimensions(page):
    """The two pages mirror each other; a dial added to one only is a trap."""
    html = (REPO / "app/static" / page).read_text()
    for control in ("settingCatVision", "settingCatVisionRear", "settingCatVisionHalfWidth"):
        assert f'id="{control}"' in html, f"{page} is missing {control}"
    assert 'id="catVisionBoxReadout"' in html


@pytest.mark.parametrize("script", ["app.js", "settings.js"])
def test_both_settings_scripts_send_and_load_all_three(script):
    js = (REPO / "app/static" / script).read_text()
    assert "vision_rear: parseIntDefault(settingCatVisionRear" in js
    assert "vision_half_width: parseIntDefault(settingCatVisionHalfWidth" in js
    assert "data.cat.vision_rear" in js
    assert "data.cat.vision_half_width" in js


@pytest.mark.parametrize("page", ["index.html", "settings.html"])
def test_the_settings_pages_no_longer_describe_a_cone(page):
    """Stale copy from two shapes ago. The FOV has never been a cone since."""
    html = (REPO / "app/static" / page).read_text().lower()
    assert "cone distance" not in html


def test_the_manual_states_the_membership_test_symbolically():
    """The actor must be taught to read the bounds, not to memorise them."""
    manual = (REPO / "actors/cat/actor_skills.md").read_text()
    assert "-vision_rear <= forward <= vision_radius" in manual
    assert "|offaxis| <= vision_half_width" in manual
    assert "vision_half_width" in manual and "vision_rear" in manual
