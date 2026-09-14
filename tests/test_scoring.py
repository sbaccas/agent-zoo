"""Regression tests for per-cat scoring and its delivery to the agent.

The score exists so a cat can infer whether its own slash connected, by diffing
the counter across consecutive observations. That inference is only sound if the
counter is attributed per cat, survives rewinds, and reaches the agent through
*both* observation paths. Each of those is pinned down below.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.game.simulation import Simulation
from app.main import app
from app.models.entity import Direction, EntityType, Position
from app.models.simulation import SimulationConfig


@pytest.fixture(autouse=True)
def reset_admin_simulations():
    import app.api.admin as admin
    admin.default_sim_id = None
    admin.simulations.clear()


def _empty_sim() -> Simulation:
    return Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))


async def _attack_once(sim, attacker, idle_ids):
    """Drive exactly one scheduler step in which `attacker` slashes.

    Uses the HTTP agent path with a mocked response so the action is chosen by
    the test rather than the heuristic, which is random and would make the
    outcome non-deterministic. Everything in `idle_ids` is parked in manual mode
    so it neither acts nor flees.
    """
    for entity_id in idle_ids:
        sim.entity_manager.set_agent_mode(entity_id, "manual")
    sim.entity_manager.set_agent_mode(attacker.id, "http", "http://mock.invalid/act")
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"action": "attack", "thought": "strike"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        await sim.step()


@pytest.mark.asyncio
async def test_score_is_attributed_to_the_cat_that_made_the_kill():
    """Each cat's score counts only its own kills, never the board's total.

    This is the load-bearing guarantee. `captures` is a single global counter,
    so if the agent were shown that number it would credit itself for a rival's
    kill on the very tick it whiffed -- reinforcing exactly the wrong behaviour.

    Both cats must score here. With only one killer the global total and that
    cat's own score are both 1, so the two are indistinguishable and the bug
    slips through. After two kills by different cats they diverge: the global
    total reads 2 while each cat's own score must still read 1.
    """
    sim = _empty_sim()
    cat_a = sim.entity_manager.spawn_entity(
        EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST
    )
    cat_b = sim.entity_manager.spawn_entity(
        EntityType.CAT, pos=Position(x=30, y=30), facing=Direction.EAST
    )
    mouse_a = sim.entity_manager.spawn_entity(
        EntityType.MOUSE, pos=Position(x=11, y=10), facing=Direction.WEST
    )
    mouse_b = sim.entity_manager.spawn_entity(
        EntityType.MOUSE, pos=Position(x=31, y=30), facing=Direction.WEST
    )
    mice_ids = [mouse_a.id, mouse_b.id]

    # Tick 1: only cat_a may act.
    await _attack_once(sim, cat_a, idle_ids=[cat_b.id] + mice_ids)
    assert mouse_a.alive is False, "setup failed: cat_a's slash did not connect"
    assert sim.scheduler.captures == 1
    assert sim.scheduler.scores[cat_a.id] == 1
    assert sim.scheduler.scores.get(cat_b.id, 0) == 0, (
        "cat_b was credited for a kill it did not make"
    )

    # Tick 2: only cat_b may act. The global counter now moves to 2.
    await _attack_once(sim, cat_b, idle_ids=[cat_a.id] + mice_ids)
    assert mouse_b.alive is False, "setup failed: cat_b's slash did not connect"
    assert sim.scheduler.captures == 2

    assert sim.scheduler.scores[cat_a.id] == 1, (
        "cat_a's score moved when cat_b scored -- it is tracking the global total"
    )
    assert sim.scheduler.scores[cat_b.id] == 1, (
        "cat_b was credited with cat_a's kill as well as its own"
    )


@pytest.mark.asyncio
async def test_scoreboard_lists_every_cat_including_those_on_zero():
    """A cat absent from `scores` has not scored; it must still be listed.

    Otherwise the leaderboard would grow as cats score, and a rival vanishing
    from the payload would be indistinguishable from a rival at zero.
    """
    sim = _empty_sim()
    a = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=5, y=5))
    b = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=6, y=6))
    sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=20, y=20))

    board = sim.scheduler.get_scoreboard()
    assert board == {a.id: 0, b.id: 0}, "mice must not appear; zero-scorers must"

    sim.scheduler.scores[a.id] = 3
    assert sim.scheduler.get_scoreboard() == {a.id: 3, b.id: 0}


@pytest.mark.asyncio
async def test_step_back_rewinds_scores_in_lockstep_with_entities():
    """Rewinding must undo the score, not just the corpse.

    If scores survived a step-back the mouse would revive while the kill stayed
    on the board, so repeated rewinds would inflate the score indefinitely.
    """
    sim = _empty_sim()
    killer = sim.entity_manager.spawn_entity(
        EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST
    )
    mouse = sim.entity_manager.spawn_entity(
        EntityType.MOUSE, pos=Position(x=11, y=10), facing=Direction.WEST
    )

    await _attack_once(sim, killer, idle_ids=[mouse.id])
    assert sim.scheduler.scores[killer.id] == 1

    await sim.step_back()

    assert sim.scheduler.scores.get(killer.id, 0) == 0
    assert sim.scheduler.captures == 0
    revived = [e for e in sim.entity_manager.get_alive_entities() if e.type == EntityType.MOUSE]
    assert len(revived) == 1, "mouse came back but the score did not rewind with it"


@pytest.mark.asyncio
async def test_snapshot_scores_are_copied_not_aliased():
    """The snapshot must hold its own dict, or the rewind target mutates itself."""
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))

    sim._save_snapshot()
    sim.scheduler.scores[cat.id] = 7

    assert sim.history[-1].scores.get(cat.id, 0) == 0


@pytest.mark.asyncio
async def test_reset_clears_scores():
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))
    sim.scheduler.scores[cat.id] = 5

    await sim.reset()

    assert sim.scheduler.scores == {}
    assert sim.scheduler.captures == 0


@pytest.mark.asyncio
async def test_score_present_in_scheduler_observation_payload():
    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))
    other = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=30, y=30))
    sim.scheduler.scores[cat.id] = 4

    payload = sim.scheduler._build_observation_payload(cat)

    assert payload["score"] == 4
    assert payload["scoreboard"] == {cat.id: 4, other.id: 0}


@pytest.mark.asyncio
async def test_both_observation_builders_report_the_same_score():
    """The WS and HTTP paths build observations with separate, duplicated code.

    The cat consumes whichever one is active, so a field added to one and missed
    in the other produces a silent, mode-dependent regression.
    """
    import app.api.admin as admin

    sim = _empty_sim()
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))
    rival = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=30, y=30))
    sim.scheduler.scores[cat.id] = 6
    sim.scheduler.scores[rival.id] = 2

    admin.simulations[sim.simulation_id] = sim
    admin.default_sim_id = sim.simulation_id

    ws_payload = sim.scheduler._build_observation_payload(cat)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/simulations/{sim.simulation_id}/agents/{cat.id}/observation"
        )
    assert res.status_code == 200
    http_payload = res.json()

    assert http_payload["score"] == ws_payload["score"] == 6
    assert http_payload["scoreboard"] == ws_payload["scoreboard"] == {cat.id: 6, rival.id: 2}
