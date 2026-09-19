"""Aim assist: the engine hands the agent the strike-zone test already solved.

The switch publishes three fields per visible entity -- ``forward``,
``offaxis``, ``in_strike_zone`` -- computed in the observer's own frame. It
grants no capability. Every one of those numbers was already derivable from the
absolute coordinates the agent has always received; the feature only removes the
requirement to derive them.

Why it exists, in one measurement. Over a single game the model cat *created
more chances than the scripted cat* (39 zone chances vs 32) and still finished
behind on kills (33 vs 37), because it converted 72% of them against the
scripted cat's 100%. The losses split evenly across both ends of one rule: 13
swings taken at targets the model had itself computed as out of range, and 11
legal chances declined at ``|offaxis| == 1`` on the belief a shot must be dead
ahead. The scripted cat is not a better tactician -- it evaluates
``1 <= forward <= attack_range and |offaxis| <= 1`` in code.

So the rule now has four implementations that must agree:

  1. ``World.get_slash_aoe_tiles``   -- the engine; decides who dies
  2. ``World.strike_geometry``       -- this feature; decides what the agent is told
  3. ``heuristic.in_strike_zone``    -- the reflex; decides when to swing
  4. ``getSlashAoeTiles`` in app.js  -- the canvas; decides what you see

(4) is already pinned against (1) and (3) in test_attack_zone.py. These tests
pin (2) against (1) and (3), by brute force rather than by restating the rule --
a second implementation of one predicate is exactly where silent drift lives.
"""

import pytest
from starlette.testclient import TestClient

from actors.cat.heuristic import decompose_offset
from app.game.world import World
from app.main import app
from app.models.entity import Direction, Position
from app.models.simulation import (
    SimulationConfig,
    SpeciesConfig,
    Topology,
    UpdateSpeciesConfigRequest,
)

FACINGS = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]


# --------------------------------------------------------------------------
# The predicate itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("topology", [Topology.BOUNDED, Topology.TORUS])
def test_the_published_predicate_matches_the_engines_tile_set(topology):
    """`in_strike_zone` must agree with the tiles that actually kill.

    Brute force over every facing, every range 0..5 and every offset in a 21x21
    window, rather than a handful of hand-picked cases: the two failure modes
    this feature exists to fix are both *boundary* errors, so the boundary is
    the only interesting part of the domain.
    """
    w = World(60, 60, topology)
    pos = Position(x=30, y=30)
    for facing in FACINGS:
        for rng in range(0, 6):
            killed = set(w.get_slash_aoe_tiles(pos, facing, rng))
            for tx in range(20, 41):
                for ty in range(20, 41):
                    told = w.strike_geometry(pos, facing, Position(x=tx, y=ty), rng)
                    assert told["in_strike_zone"] == ((tx, ty) in killed), (
                        f"{topology.value} {facing.value} range={rng} "
                        f"target=({tx},{ty}) told={told}"
                    )


def test_the_published_geometry_matches_the_reflexs_own_decomposition():
    """Sign included, not just magnitude.

    `in_strike_zone` only depends on ``|offaxis|``, so the two could agree on
    every hit/miss decision while disagreeing on which side the target is on.
    That would matter: the prompt now tells the model offaxis is signed, and the
    reflex reasons in the same frame. A sign flip between them would put the
    agent's two halves in disagreement about where a mouse is without either
    ever misjudging whether it could be hit.
    """
    w = World(60, 60, Topology.BOUNDED)
    pos = Position(x=30, y=30)
    for facing in FACINGS:
        for dx in range(-5, 6):
            for dy in range(-5, 6):
                mine = w.strike_geometry(
                    pos, facing, Position(x=30 + dx, y=30 + dy), 4
                )
                theirs = decompose_offset(dx, dy, facing.value)
                assert (mine["forward"], mine["offaxis"]) == theirs, (
                    f"{facing.value} offset=({dx},{dy})"
                )


@pytest.mark.parametrize("facing,right_offset", [
    (Direction.NORTH, (1, 0)),    # facing north, right hand points east
    (Direction.EAST, (0, 1)),     # facing east,  right hand points south
    (Direction.SOUTH, (-1, 0)),   # facing south, right hand points west
    (Direction.WEST, (0, -1)),    # facing west,  right hand points north
])
def test_offaxis_is_positive_to_the_cats_right(facing, right_offset):
    """Pins the documented sign convention, which follows (px, py) = (-dy, dx).

    Documented because the prompt states it, and a reader checking the prompt
    against the code should not have to re-derive a cross product to confirm it.
    """
    w = World(60, 60, Topology.BOUNDED)
    dx, dy = right_offset
    g = w.strike_geometry(
        Position(x=30, y=30), facing, Position(x=30 + dx, y=30 + dy), 4
    )
    assert g["offaxis"] == 1
    assert g["forward"] == 0


def test_a_target_behind_reports_negative_forward():
    w = World(60, 60, Topology.BOUNDED)
    g = w.strike_geometry(
        Position(x=30, y=30), Direction.EAST, Position(x=27, y=30), 4
    )
    assert g["forward"] == -3
    assert g["in_strike_zone"] is False


def test_a_torus_target_across_the_seam_is_measured_the_short_way():
    """Plain subtraction gets this wrong, and only here.

    On a 100-wide torus a mouse at x=1 seen from x=99 is 2 tiles east. Naive
    subtraction calls it 98 tiles west: not merely out of range, but behind.
    The engine's own tile set wraps, so without this the feature would confi-
    dently report `in_strike_zone: False` for a mouse the cat was about to kill.
    """
    t = World(100, 100, Topology.TORUS)
    g = t.strike_geometry(
        Position(x=99, y=50), Direction.EAST, Position(x=1, y=50), 4
    )
    assert (g["forward"], g["offaxis"], g["in_strike_zone"]) == (2, 0, True)

    # And the same seam southbound, to catch an axis-specific mistake.
    g = t.strike_geometry(
        Position(x=50, y=99), Direction.SOUTH, Position(x=50, y=2), 4
    )
    assert (g["forward"], g["offaxis"], g["in_strike_zone"]) == (3, 0, True)


def test_a_bounded_board_does_not_wrap():
    """The seam fix must not leak into the default topology.

    On a bounded 100-wide board, x=99 to x=1 really is 98 tiles away.
    """
    b = World(100, 100, Topology.BOUNDED)
    g = b.strike_geometry(
        Position(x=99, y=50), Direction.EAST, Position(x=1, y=50), 4
    )
    assert g["forward"] == -98
    assert g["in_strike_zone"] is False


# --------------------------------------------------------------------------
# The switch
# --------------------------------------------------------------------------

def test_aim_assist_ships_off():
    """The archive's baseline was recorded unassisted.

    Defaulting this on would silently make every future run incomparable with
    every prompt already on disk, which is the one thing an A/B switch must not
    do.
    """
    assert SpeciesConfig().aim_assist is False
    assert SimulationConfig().species["cat"].aim_assist is False
    assert SimulationConfig().species["mouse"].aim_assist is False


def test_the_patch_model_accepts_the_toggle():
    """Absent from UpdateSpeciesConfigRequest, the PATCH would silently drop it.

    `update_species_config` iterates `model_dump(exclude_unset=True)`, so an
    unlisted field is not rejected -- it is ignored, and the endpoint answers
    200 while changing nothing.
    """
    assert UpdateSpeciesConfigRequest(aim_assist=True).aim_assist is True
    assert UpdateSpeciesConfigRequest(aim_assist=False).aim_assist is False
    assert UpdateSpeciesConfigRequest().aim_assist is None


# --------------------------------------------------------------------------
# End to end: settings tab -> engine -> the payload the agent reads
# --------------------------------------------------------------------------

@pytest.fixture
def client_sim():
    c = TestClient(app)
    sid = c.post("/api/simulations", json={
        "width": 60, "height": 60, "populations": {"cats": 1, "mice": 3},
    }).json()["simulation_id"]
    return c, sid


def test_the_observation_omits_the_geometry_while_the_switch_is_off(client_sim):
    """Nothing is annotated until the operator asks for it.

    Checked on the REST endpoint because that is what a TestClient can reach.
    The live agent runs in `ws` mode and reads `_build_observation_payload`,
    which is where the annotation is actually attached; the REST endpoint
    returns typed `Entity` objects and is used only for bootstrap and for the
    workbench's peek, so it deliberately carries no geometry either way.
    """
    c, sid = client_sim
    caps = c.get(f"/api/simulations/{sid}/agents/cat-1/observation").json()["capabilities"]
    assert caps.get("aim_assist", False) is False


def test_a_patch_turns_it_on_live(client_sim):
    """The whole point of putting it in settings: flip it mid-game."""
    c, sid = client_sim

    r = c.patch(f"/api/simulations/{sid}/species/cat", json={"aim_assist": True})
    assert r.status_code == 200
    assert r.json()["aim_assist"] is True

    species = c.get(f"/api/simulations/{sid}/species").json()
    assert species["cat"]["aim_assist"] is True
    # Prey must not be swept along: mice have no attack to aim.
    assert species["mouse"]["aim_assist"] is False

    r = c.patch(f"/api/simulations/{sid}/species/cat", json={"aim_assist": False})
    assert r.json()["aim_assist"] is False


def test_toggling_it_leaves_every_other_dial_alone(client_sim):
    """A PATCH carrying only this field must not disturb the tunables.

    Worth pinning because the settings UI sends the whole cat payload on save,
    so a regression here would show up as vision or cooldowns quietly resetting
    the first time someone ticked the box.
    """
    c, sid = client_sim
    before = c.get(f"/api/simulations/{sid}/species").json()["cat"]

    c.patch(f"/api/simulations/{sid}/species/cat", json={"aim_assist": True})
    after = c.get(f"/api/simulations/{sid}/species").json()["cat"]

    for field, was in before.items():
        if field == "aim_assist":
            continue
        assert after[field] == was, f"{field} changed: {was} -> {after[field]}"


def test_the_state_payload_exposes_the_flag_to_the_ui(client_sim):
    """The settings tab reads this to render the checkbox.

    Without it the control would load unticked on every page open regardless of
    the live setting, and the next save would turn the feature off -- the exact
    class of bug the hand-maintained cache stamps caused for the vision box.
    """
    c, sid = client_sim
    c.patch(f"/api/simulations/{sid}/species/cat", json={"aim_assist": True})
    species = c.get(f"/api/simulations/{sid}/state").json()["species"]
    assert species["cat"]["aim_assist"] is True


# --------------------------------------------------------------------------
# The payload the live agent actually reads
#
# The running cat is registered in `ws` mode, so every decision it makes is
# built from `_build_observation_payload`. The tests above exercise the config
# plumbing; these exercise the thing the feature is for.
# --------------------------------------------------------------------------

def _sim_with_cat_and_mouse(aim_assist, cat_at=(10, 10), mouse_at=(12, 10),
                            facing=Direction.EAST):
    from app.game.simulation import Simulation
    from app.models.entity import EntityType

    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    sim.entity_manager.species_config["cat"].aim_assist = aim_assist
    cat = sim.entity_manager.spawn_entity(
        EntityType.CAT, pos=Position(x=cat_at[0], y=cat_at[1])
    )
    cat.facing = facing
    mouse = sim.entity_manager.spawn_entity(
        EntityType.MOUSE, pos=Position(x=mouse_at[0], y=mouse_at[1])
    )
    return sim, cat, mouse


GEOMETRY_KEYS = {"forward", "offaxis", "in_strike_zone"}


def test_the_ws_payload_is_unannotated_while_the_switch_is_off():
    """The baseline condition: absolute coordinates and nothing else.

    This is the observation shape every archived prompt was recorded under, so
    it has to stay reachable for the A/B to mean anything.
    """
    sim, cat, _ = _sim_with_cat_and_mouse(aim_assist=False)
    payload = sim.scheduler._build_observation_payload(cat)

    seen = payload["visible_entities"][0]
    assert GEOMETRY_KEYS.isdisjoint(seen)
    assert payload["capabilities"]["aim_assist"] is False


def test_the_ws_payload_carries_the_geometry_while_the_switch_is_on():
    sim, cat, _ = _sim_with_cat_and_mouse(aim_assist=True)
    payload = sim.scheduler._build_observation_payload(cat)

    seen = payload["visible_entities"][0]
    assert GEOMETRY_KEYS.issubset(seen)
    # Mouse two tiles due east of an east-facing cat, default range 4.
    assert (seen["forward"], seen["offaxis"]) == (2, 0)
    assert seen["in_strike_zone"] is True
    assert payload["capabilities"]["aim_assist"] is True


def test_the_absolute_position_survives_the_annotation():
    """Geometry answers "can I hit it", not "where is it".

    Navigation, exploration and the evaluator's coverage bookkeeping all still
    read `position`, so the annotation is additive rather than a replacement.
    """
    sim, cat, _ = _sim_with_cat_and_mouse(aim_assist=True)
    seen = sim.scheduler._build_observation_payload(cat)["visible_entities"][0]
    assert seen["position"] == {"x": 12, "y": 10}
    assert seen["id"] and seen["type"] == "mouse"


def test_prey_are_never_annotated():
    """Mice have no attack, so a strike zone would be meaningless to them.

    They also use a radial sight model with no facing-relative frame at all, so
    `forward` would not even be well defined.
    """
    sim, _, mouse = _sim_with_cat_and_mouse(aim_assist=True)
    sim.entity_manager.species_config["mouse"].aim_assist = True

    payload = sim.scheduler._build_observation_payload(mouse)
    assert payload["visible_entities"], "mouse should see the cat"
    for seen in payload["visible_entities"]:
        assert GEOMETRY_KEYS.isdisjoint(seen)


def test_the_switch_can_be_flipped_between_ticks():
    """Operators toggle this mid-game from the settings tab.

    Same simulation, same entities, two consecutive observations: the feature
    has to take effect without a restart, because the whole point is to A/B it
    against a run already in progress.
    """
    sim, cat, _ = _sim_with_cat_and_mouse(aim_assist=False)
    assert GEOMETRY_KEYS.isdisjoint(
        sim.scheduler._build_observation_payload(cat)["visible_entities"][0]
    )

    sim.entity_manager.species_config["cat"].aim_assist = True
    assert GEOMETRY_KEYS.issubset(
        sim.scheduler._build_observation_payload(cat)["visible_entities"][0]
    )

    sim.entity_manager.species_config["cat"].aim_assist = False
    assert GEOMETRY_KEYS.isdisjoint(
        sim.scheduler._build_observation_payload(cat)["visible_entities"][0]
    )


def test_the_published_range_follows_a_retuned_attack_range():
    """`in_strike_zone` is computed against the live config, not a constant.

    A mouse 5 ahead is out of reach at the default range of 4 and in reach at 6.
    If this read a hardcoded number the feature would start lying the first time
    anyone touched the reach dial -- and reach is already tuned routinely.
    """
    sim, cat, _ = _sim_with_cat_and_mouse(aim_assist=True, mouse_at=(15, 10))

    seen = sim.scheduler._build_observation_payload(cat)["visible_entities"][0]
    assert seen["forward"] == 5 and seen["in_strike_zone"] is False

    sim.entity_manager.species_config["cat"].attack_range = 6
    seen = sim.scheduler._build_observation_payload(cat)["visible_entities"][0]
    assert seen["forward"] == 5 and seen["in_strike_zone"] is True


@pytest.mark.parametrize("mouse_at,expected", [
    ((12, 9), True),    # offaxis -1: legal, and the case the model kept declining
    ((12, 10), True),   # dead ahead
    ((12, 11), True),   # offaxis +1: legal
    ((12, 8), False),   # offaxis -2: genuinely outside
    ((12, 12), False),  # offaxis +2: genuinely outside
])
def test_the_lateral_bound_is_reported_inclusively(mouse_at, expected):
    """The zone is three tiles wide, and the middle column is not the whole zone.

    Pinned as its own case because misreading this bound is the single largest
    measured loss: 16 of 18 declined chances in one game sat at |offaxis| == 1,
    every one of them a legal shot.
    """
    sim, cat, _ = _sim_with_cat_and_mouse(aim_assist=True, mouse_at=mouse_at)
    seen = sim.scheduler._build_observation_payload(cat)["visible_entities"][0]
    assert seen["in_strike_zone"] is expected


@pytest.mark.parametrize("mouse_at", [
    (12, 9), (12, 10), (12, 11), (11, 10), (14, 10), (12, 8), (16, 10),
])
def test_in_strike_zone_predicts_who_actually_dies(mouse_at):
    """The claim under test, stated as bluntly as it can be.

    The agent is being told "swing and this connects". If that is ever wrong the
    feature is worse than useless -- it would be authoritative *and* wrong,
    which is strictly worse than the raw coordinates it replaced. So this
    asserts against the engine's real kill resolution rather than against the
    tile set, closing the loop through `get_slash_aoe_tiles` to the hazard zone
    that does the killing.
    """
    sim, cat, mouse = _sim_with_cat_and_mouse(aim_assist=True, mouse_at=mouse_at)

    seen = sim.scheduler._build_observation_payload(cat)["visible_entities"][0]
    promised = seen["in_strike_zone"]

    lethal = set(sim.world.get_slash_aoe_tiles(
        cat.position, cat.facing,
        sim.entity_manager.species_config["cat"].attack_range,
    ))
    actually_dies = (mouse.position.x, mouse.position.y) in lethal

    assert promised == actually_dies, (
        f"told in_strike_zone={promised} for a mouse at {mouse_at} that "
        f"{'would' if actually_dies else 'would not'} have died"
    )


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------

def _prompt_with(caps_extra, entities):
    from actors.cat.actor import build_decision_prompt
    from actors.models import Observation

    caps = {
        "vision_radius": 10, "vision_rear": 2, "vision_half_width": 3,
        "attack_range": 4, "move_cooldown": 3, "attack_cooldown": 3,
        "lunge_cooldown": 2, "weight": 10,
    }
    caps.update(caps_extra)
    obs = Observation(
        tick=5, round=1,
        self={"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "east",
              "action_cooldown": 0, "can_act": True},
        visible_entities=entities,
        legal_actions=["north", "south", "east", "west", "attack", "lunge", "stay"],
        capabilities=caps, score=0, scoreboard={},
    )
    # agent_id by keyword: the second positional parameter is `history`.
    return build_decision_prompt(obs, agent_id="cat-1")


def test_the_prompt_stays_silent_when_the_fields_are_absent():
    """A standing order to trust fields that are not there is worse than none."""
    p = _prompt_with({}, [{"id": "mouse-1", "position": {"x": 12, "y": 10}}])
    assert "aim_assist" not in p
    assert "in_strike_zone" not in p


def test_the_prompt_explains_the_fields_when_they_are_present():
    """It must state the rule, and state which source wins.

    Both halves matter. The model's arithmetic is good -- a forensic pass found
    123 of 135 self-reported forward distances exactly correct -- so an
    unexplained extra field reads as a second opinion to be weighed rather than
    an answer. The text has to say plainly that it is the answer.
    """
    p = _prompt_with(
        {"aim_assist": True},
        [{"id": "mouse-1", "position": {"x": 12, "y": 10},
          "forward": 2, "offaxis": 0, "in_strike_zone": True}],
    )
    assert "aim_assist: ON" in p
    assert "in_strike_zone" in p
    # The lateral bound stated as inclusive, since believing otherwise is what
    # forfeited 11 legal chances.
    assert "|offaxis| <= 1" in p
    assert "3 tiles wide" in p
    # And the attack range interpolated live, not hardcoded.
    assert "1 <= forward <= 4" in p


def test_the_prompt_quotes_the_live_attack_range_not_a_constant():
    """If the operator retunes reach, the stated rule must follow it."""
    p = _prompt_with(
        {"aim_assist": True, "attack_range": 7},
        [{"id": "mouse-1", "position": {"x": 12, "y": 10}}],
    )
    assert "1 <= forward <= 7" in p
