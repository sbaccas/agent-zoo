"""Regression tests for the per-cat box score.

The box score exists to give post-game analysis a fixed vocabulary, so the
counters are only useful if they mean exactly one thing. The cases below pin
down the definitions that are easy to get subtly wrong: a slash whose blade
kills a tick after it lands is not a whiff, a chance the cat declined is still
a chance, and a rewind must take the statistics back with the board.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.game.simulation import Simulation
from app.models.action import ActionType
from app.models.entity import Direction, EntityType, Position
from app.models.simulation import SimulationConfig


@pytest.fixture(autouse=True)
def reset_admin_simulations():
    import app.api.admin as admin
    admin.default_sim_id = None
    admin.simulations.clear()


def _empty_sim() -> Simulation:
    return Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))


def _stats_for(sim, cat_id):
    return sim.stats.get(cat_id)


def _row_for(sim, cat_id):
    return next(c for c in sim.get_boxscore()["cats"] if c["id"] == cat_id)


def _unlock(entity):
    """Clear the action lock so a test can drive several actions in a row.

    Recovery is what the cooldown counters measure elsewhere; these movement
    cases are about what a single action records, so the wait is skipped.
    """
    entity.action_cooldown = 0
    entity.locked_action = None


async def _attack_once(sim, attacker, idle_ids):
    """Drive one scheduler step in which `attacker` slashes.

    Mirrors the helper in test_scoring.py: the attacker is driven over the
    mocked HTTP path so the action is the test's choice rather than the
    heuristic's, and everything else is parked in manual mode so it stays put.
    """
    for entity_id in idle_ids:
        sim.entity_manager.set_agent_mode(entity_id, "manual")
    sim.entity_manager.set_agent_mode(attacker.id, "http", "http://mock.invalid/act")
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"action": "attack", "thought": "strike"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        await sim.step()


# --- slashing ----------------------------------------------------------


@pytest.mark.asyncio
async def test_landed_slash_scores_full_accuracy():
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10), facing=Direction.WEST)

    await _attack_once(sim, cat, idle_ids=[mouse.id])

    assert mouse.alive is False, "setup failed: the slash did not connect"
    row = _row_for(sim, cat.id)
    assert row["kills"] == 1
    assert row["slash_attempts"] == 1
    assert row["whiffs"] == 0
    assert row["slash_accuracy"] == 1.0


@pytest.mark.asyncio
async def test_slash_at_empty_air_is_a_whiff():
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=60, y=60), facing=Direction.WEST)

    await _attack_once(sim, cat, idle_ids=[mouse.id])

    assert mouse.alive is True
    row = _row_for(sim, cat.id)
    assert row["kills"] == 0
    assert row["slash_attempts"] == 1
    assert row["whiffs"] == 1
    assert row["slash_accuracy"] == 0.0


@pytest.mark.asyncio
async def test_lingering_blade_promotes_its_slash_out_of_the_whiff_column():
    """A kill on the tick after impact belongs to the slash that made it.

    The blade stays lethal for `attack_aoe_ticks`, so prey that walks into it
    is killed by that swing. Counting only the impact frame would file the
    swing as a whiff and then credit a kill with no attempt behind it, which
    is how an accuracy figure ends up disagreeing with the kill column.
    """
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=60, y=60), facing=Direction.WEST)

    # Tick 1: the swing connects with nothing.
    await _attack_once(sim, cat, idle_ids=[mouse.id])
    assert _row_for(sim, cat.id)["whiffs"] == 1, "setup failed: expected an initial miss"

    # Walk the mouse into the still-lethal blade and let the tick resolve.
    mouse.position = Position(x=12, y=10)
    await sim.step()

    assert mouse.alive is False, "setup failed: the lingering zone did not kill"
    row = _row_for(sim, cat.id)
    assert row["kills"] == 1
    assert row["slash_attempts"] == 1, "the zone kill invented a second attempt"
    assert row["whiffs"] == 0, "the swing that eventually killed is still filed as a miss"
    assert row["slash_accuracy"] == 1.0


# --- opportunity conversion -------------------------------------------


@pytest.mark.asyncio
async def test_declined_strike_zone_chance_is_recorded():
    """Walking away from a free kill has to leave a trace.

    This is the counter that exposes the LLM cat's largest tactical leak, so a
    chance must be booked on the observation the cat acted from, not on
    whether it chose to swing.
    """
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=12, y=10), facing=Direction.WEST)
    sim.entity_manager.set_agent_mode(mouse.id, "manual")

    # Mouse sits squarely in the strike zone; the cat moves instead.
    sim.action_handler.apply_action(cat, ActionType.NORTH, current_tick=1)

    stats = _stats_for(sim, cat.id)
    assert stats.zone_chances == 1
    assert stats.zone_attempts == 0
    row = _row_for(sim, cat.id)
    assert row["zone_declined"] == 1
    assert row["zone_conversion"] == 0.0
    assert mouse.alive is True


@pytest.mark.asyncio
async def test_taken_strike_zone_chance_counts_as_converted():
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=12, y=10), facing=Direction.WEST)

    sim.action_handler.apply_action(cat, ActionType.ATTACK, current_tick=1)

    row = _row_for(sim, cat.id)
    assert row["zone_chances"] == 1
    assert row["zone_declined"] == 0
    assert row["zone_conversion"] == 1.0


# --- movement ----------------------------------------------------------


@pytest.mark.asyncio
async def test_move_and_lunge_are_counted_separately():
    """Lunge share is the stat the traversal diagnosis turns on.

    A lunge covers two tiles for two ticks of recovery against one tile for
    three, so conflating the two would hide a threefold difference in speed.
    """
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)

    sim.action_handler.apply_action(cat, ActionType.EAST, current_tick=1)
    _unlock(cat)
    sim.action_handler.apply_action(cat, ActionType.LUNGE, current_tick=2)

    stats = _stats_for(sim, cat.id)
    assert stats.moves == 1
    assert stats.lunges == 1
    assert stats.tiles_run == 3, "one tile walked plus two lunged"
    assert cat.position.x == 13
    assert _row_for(sim, cat.id)["lunge_share"] == 0.5


@pytest.mark.asyncio
async def test_lunge_into_the_boundary_is_recorded_as_a_wall_bonk():
    """The short lunge is real, and the cat over-generalises from it.

    The transcript shows the agent refusing to lunge in open ground because it
    once "did not move". Counting the genuine article separates the rare wall
    clip from the imagined risk.
    """
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=98, y=10), facing=Direction.EAST)

    sim.action_handler.apply_action(cat, ActionType.LUNGE, current_tick=1)

    stats = _stats_for(sim, cat.id)
    assert cat.position.x == 99, "setup failed: expected the lunge to clip the edge"
    assert stats.lunges == 1
    assert stats.wall_bonks == 1
    assert stats.tiles_run == 1, "a clipped lunge covers one tile, not two"


@pytest.mark.asyncio
async def test_ground_covered_counts_each_tile_once():
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)

    # Out and back: three tiles of running across two distinct tiles.
    sim.action_handler.apply_action(cat, ActionType.EAST, current_tick=1)
    _unlock(cat)
    sim.action_handler.apply_action(cat, ActionType.WEST, current_tick=2)
    _unlock(cat)
    sim.action_handler.apply_action(cat, ActionType.EAST, current_tick=3)

    row = _row_for(sim, cat.id)
    assert row["tiles_run"] == 3
    assert row["unique_tiles"] == 2
    assert row["ground_covered"] == 2 / 10000
    assert row["ticks_per_tile"] == 1.5, "three ticks spent across two tiles"


# --- tempo -------------------------------------------------------------


@pytest.mark.asyncio
async def test_free_and_locked_ticks_partition_the_game():
    """Every tick a cat is on the board it is either acting or recovering.

    The two counters are the denominators for most of the rate statistics, so
    a tick falling through both cracks would quietly skew the whole column.
    """
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 3}))
    cat_id = next(
        e.id for e in sim.entity_manager.entities.values() if e.type == EntityType.CAT
    )

    for _ in range(12):
        await sim.step()

    stats = _stats_for(sim, cat_id)
    assert stats.ticks_free + stats.ticks_locked == sim.scheduler.tick == 12
    assert stats.ticks_locked > 0, "a cat with cooldowns should have spent ticks locked"


# --- lifecycle ---------------------------------------------------------


@pytest.mark.asyncio
async def test_step_back_rewinds_the_box_score():
    """Rewinding the board must rewind its statistics too.

    Otherwise a step-back resurrects the mouse while leaving the kill on the
    books, and the box score drifts further from the board every time the
    operator scrubs backwards.
    """
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10), facing=Direction.WEST)

    await _attack_once(sim, cat, idle_ids=[mouse.id])
    assert _row_for(sim, cat.id)["kills"] == 1

    await sim.step_back()

    row = _row_for(sim, cat.id)
    assert row["kills"] == 0, "the kill outlived the rewind"
    assert row["slash_attempts"] == 0, "the slash outlived the rewind"
    assert row["zone_chances"] == 0


@pytest.mark.asyncio
async def test_step_back_rewinds_ground_covered():
    """The visited-tile set is rewound by truncation, so it needs its own case.

    Counters are plain integers, but unique tiles are restored by cutting the
    visit log back to a recorded length. A bug there would leave explored
    ground standing after the cat has been moved back off it.
    """
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    sim.entity_manager.set_agent_mode(cat.id, "manual")

    await sim.step()  # snapshot taken with the cat still on its spawn tile
    before = _row_for(sim, cat.id)["unique_tiles"]
    assert before == 0, "a manual cat takes no action, so nothing is booked yet"

    _unlock(cat)
    sim.action_handler.apply_action(cat, ActionType.EAST, current_tick=2)
    # The first recorded action books two tiles: the one departed and the one
    # reached. Only the arrival is new ground on every action after this.
    assert _row_for(sim, cat.id)["unique_tiles"] == 2

    await sim.step_back()
    assert _row_for(sim, cat.id)["unique_tiles"] == before


@pytest.mark.asyncio
async def test_reset_clears_the_box_score():
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10), facing=Direction.WEST)
    await _attack_once(sim, cat, idle_ids=[mouse.id])
    assert sim.stats.get(cat.id).kills == 1

    await sim.reset()

    assert all(s.kills == 0 for s in sim.stats.cats.values())
    assert all(s.slash_attempts == 0 for s in sim.stats.cats.values())


@pytest.mark.asyncio
async def test_recording_survives_a_reset_rebuilding_the_action_handler():
    """Reset replaces the action handler, which must keep its tracker.

    A handler rebuilt without one would leave the game looking healthy while
    silently recording nothing for the rest of the session.
    """
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 3}))
    await sim.reset()

    assert sim.action_handler.stats is sim.stats

    for _ in range(6):
        await sim.step()

    assert any(s.ticks_free > 0 for s in sim.stats.cats.values())


# --- reporting ---------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_rates_report_none_rather_than_zero():
    """A cat that has not swung has no accuracy, which is not the same as 0%.

    Reporting an unearned zero would make an idle cat look like a missing one
    and drag any comparison against it downwards.
    """
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)

    row = _row_for(sim, cat.id)
    assert row["slash_accuracy"] is None
    assert row["zone_conversion"] is None
    assert row["tiles_per_kill"] is None
    assert row["lunge_share"] is None
    assert row["kills"] == 0


@pytest.mark.asyncio
async def test_boxscore_endpoint_returns_a_row_per_cat():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/simulations/active/boxscore")

    assert resp.status_code == 200
    body = resp.json()
    assert body["arena"]["tiles"] == body["arena"]["width"] * body["arena"]["height"]
    assert len(body["cats"]) == 10
    expected = {
        "id", "mode", "alive", "position", "kills", "kills_per_100_ticks",
        "tiles_per_kill", "slash_attempts", "slash_accuracy", "whiffs",
        "multi_kills", "zone_chances", "zone_conversion", "zone_declined",
        "tiles_run", "unique_tiles", "ground_covered", "speed_tiles_per_tick",
        "ticks_per_tile", "moves", "lunges", "lunge_share", "wall_bonks",
        "ticks_free", "ticks_locked", "locked_pct", "prey_in_sight_pct",
    }
    assert expected <= set(body["cats"][0]), "box score contract lost a field"


@pytest.mark.asyncio
async def test_leader_is_none_while_the_cats_are_level():
    sim = _empty_sim()
    sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=40, y=40), facing=Direction.EAST)
    assert sim.get_boxscore()["leader"] is None, "a scoreless tie has no leader"


@pytest.mark.asyncio
async def test_boxscore_includes_population_when_disclosure_enabled():
    sim = _empty_sim()
    sim.config.populations["mice"] = 50
    sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=1, y=1))
    sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=2, y=2))
    sim.scheduler.captures = 5

    bs = sim.get_boxscore()
    assert bs["mice_alive"] == 2
    assert bs["mice_initial"] == 50
    assert bs["captures"] == 5

    # When toggled off
    sim.config.disclosure.coach_sees_population = False
    bs_hidden = sim.get_boxscore()
    assert "mice_alive" not in bs_hidden
    assert "mice_initial" not in bs_hidden
    assert "captures" not in bs_hidden


@pytest.mark.asyncio
async def test_boxscore_tracks_absorbed_kills():
    sim = _empty_sim()
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=12, y=10), facing=Direction.WEST)
    cat1.hp = 100
    cat2.hp = 34

    # Give cat2 10 prior kills
    sim.scheduler.scores[cat2.id] = 10
    sim.stats.get(cat2.id).kills = 10
    sim.entity_manager.species_config["cat"].absorption = True

    # cat1 eliminates cat2
    await _attack_once(sim, cat1, [cat2.id])

    bs = sim.get_boxscore()
    row1 = next(c for c in bs["cats"] if c["id"] == cat1.id)
    assert row1["kills"] == 1
    assert row1["absorbed_kills"] == 10
    assert row1["score"] == 11
    assert bs["leader"] == cat1.id


@pytest.mark.asyncio
async def test_boxscore_reconciles_uncredited_absorbed_kills():
    sim = _empty_sim()
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=12, y=10), facing=Direction.WEST)
    cat3 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=13, y=10), facing=Direction.WEST)

    sim.stats.get(cat2.id).kills = 11
    sim.stats.get(cat3.id).kills = 37
    sim.scheduler.scores[cat1.id] = 50
    sim.stats.get(cat1.id).kills = 50

    # Simulate deaths where absorbed_score was uncredited (0)
    sim.scheduler.deaths.append({
        "id": cat2.id,
        "type": "cat",
        "tick": 249,
        "round": 2,
        "killed_by": cat1.id,
        "source": "slash impact",
        "position": {"x": 12, "y": 10},
        "absorbed_score": 0,
    })
    sim.scheduler.deaths.append({
        "id": cat3.id,
        "type": "cat",
        "tick": 1352,
        "round": 20,
        "killed_by": cat1.id,
        "source": "slash impact",
        "position": {"x": 13, "y": 10},
        "absorbed_score": 0,
    })

    bs = sim.get_boxscore()
    row1 = next(c for c in bs["cats"] if c["id"] == cat1.id)
    assert row1["absorbed_kills"] == 48
    assert row1["score"] == 98


