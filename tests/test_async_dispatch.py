"""Regression tests for the asynchronous observation dispatch contract.

The simulation never waits for an agent. It hands out observations at the end of
a tick and collects whatever came back at the start of the next one, so an agent
has exactly one tick period to answer and a miss costs the board nothing.

Two failure modes drove these cases and both were live before this contract
existed. The first is a stale answer: the action buffer was unstamped, so a reply
computed several ticks ago executed as readily as a fresh one and the cat acted
on a board that had already moved. The second is a fallback that never ran: the
missed-deadline path assigned `stay`, which is a legal action and therefore
truthy, so the heuristic sitting underneath it was unreachable for every
WebSocket cat that ever missed a window.
"""

import time

import pytest

from app.game.simulation import Simulation
from app.game.stats import MODEL_ENGINE
from app.game.ws_manager import (
    COLLECT_FRESH,
    COLLECT_MISSING,
    COLLECT_NO_AGENT,
    COLLECT_STALE,
    WsAgentManager,
)
from app.models.entity import EntityType
from app.models.simulation import SimulationConfig


@pytest.fixture(autouse=True)
def reset_admin_simulations():
    import app.api.admin as admin
    admin.default_sim_id = None
    admin.simulations.clear()


class _FakeSocket:
    """Records what was sent without needing a live connection."""

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _manager_with_agent(agent_id="cat-1"):
    mgr = WsAgentManager()
    sock = _FakeSocket()
    mgr.active_connections[agent_id] = sock
    return mgr, sock


# -- freshness ----------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_records_the_outstanding_tick():
    mgr, _ = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 7})
    assert mgr.last_dispatched_tick["cat-1"] == 7


@pytest.mark.asyncio
async def test_an_answer_to_the_current_tick_is_collected():
    mgr, _ = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 7})
    mgr.receive_action("cat-1", {"tick": 7, "action": "lunge"})

    result = mgr.collect_action("cat-1")

    assert result.status == COLLECT_FRESH
    assert result.action["action"] == "lunge"


@pytest.mark.asyncio
async def test_an_answer_to_an_older_tick_is_discarded():
    """The whole point of the stamp: a late reply must not drive the board."""
    mgr, _ = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 7})
    # The agent was still thinking when tick 8 was dispatched, and answers 7 late.
    await mgr.send_observation("cat-1", {"tick": 8})
    mgr.receive_action("cat-1", {"tick": 7, "action": "lunge"})

    result = mgr.collect_action("cat-1")

    assert result.status == COLLECT_STALE
    assert result.action is None
    assert result.answered_tick == 7
    assert result.expected_tick == 8


@pytest.mark.asyncio
async def test_silence_is_reported_as_missing_not_stale():
    """A slow agent and a confused one need different fixes, so they are named apart."""
    mgr, _ = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 7})

    result = mgr.collect_action("cat-1")

    assert result.status == COLLECT_MISSING
    assert result.action is None


def test_no_connection_is_reported_as_no_agent():
    mgr = WsAgentManager()
    assert mgr.collect_action("cat-1").status == COLLECT_NO_AGENT


@pytest.mark.asyncio
async def test_an_answer_is_consumed_exactly_once():
    """A lingering answer would silently replay on the following tick."""
    mgr, _ = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 7})
    mgr.receive_action("cat-1", {"tick": 7, "action": "lunge"})

    assert mgr.collect_action("cat-1").status == COLLECT_FRESH
    assert mgr.collect_action("cat-1").status == COLLECT_MISSING


@pytest.mark.asyncio
async def test_an_agent_that_omits_the_tick_is_still_heard():
    """Third-party agents predate the echo convention and must not be muted."""
    mgr, _ = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 7})
    mgr.receive_action("cat-1", {"action": "stay"})

    assert mgr.collect_action("cat-1").status == COLLECT_FRESH


@pytest.mark.asyncio
async def test_disconnect_clears_the_outstanding_question():
    mgr, _ = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 7})
    sock = mgr.active_connections["cat-1"]
    mgr.connection_agents[sock] = "cat-1"

    await mgr.unregister(sock, sim=None)

    assert "cat-1" not in mgr.last_dispatched_tick


# -- the budget on the wire --------------------------------------------


@pytest.mark.asyncio
async def test_the_budget_is_sent_as_a_duration():
    """Not a timestamp: the agent is a separate process with its own clock."""
    mgr, sock = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 1}, budget_sec=4.0)
    assert sock.sent[0]["budget_sec"] == 4.0


@pytest.mark.asyncio
async def test_an_unknown_budget_is_omitted_rather_than_guessed():
    mgr, sock = _manager_with_agent()
    await mgr.send_observation("cat-1", {"tick": 1}, budget_sec=None)
    assert "budget_sec" not in sock.sent[0]


# -- scheduler behaviour ------------------------------------------------


def _sim_with_ws_cat():
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 2}))
    cat = next(
        e for e in sim.entity_manager.get_alive_entities() if e.type == EntityType.CAT
    )
    from app.game.ws_manager import ws_agent_manager

    ws_agent_manager.active_connections[cat.id] = _FakeSocket()
    sim.entity_manager.set_agent_mode(cat.id, "ws")
    return sim, cat


def _cleanup_ws(cat_id):
    from app.game.ws_manager import ws_agent_manager

    ws_agent_manager.active_connections.pop(cat_id, None)
    ws_agent_manager.action_buffers.pop(cat_id, None)
    ws_agent_manager.last_dispatched_tick.pop(cat_id, None)


@pytest.mark.asyncio
async def test_a_missed_window_hands_the_turn_to_the_heuristic():
    """The `stay` default that used to sit here made this fallback unreachable."""
    sim, cat = _sim_with_ws_cat()
    try:
        await sim.scheduler.execute_simulation_step()
        await sim.scheduler.execute_simulation_step()

        stats = sim.stats.get(cat.id)
        assert stats.decisions_by_fallback >= 1
        assert stats.decisions_by_model == 0
        # Counting a fallback is not the same as playing one. `ticks_free` is
        # only incremented when an action actually reaches the action handler,
        # so this is what distinguishes a covered turn from a skipped one.
        assert stats.ticks_free >= 1
    finally:
        _cleanup_ws(cat.id)


@pytest.mark.asyncio
async def test_a_silent_agent_does_not_slow_the_tick():
    """No budget is burned waiting. The step is bounded by the board, not the agent."""
    sim, cat = _sim_with_ws_cat()
    try:
        started = time.monotonic()
        await sim.scheduler.execute_simulation_step()
        elapsed = time.monotonic() - started
        # The old blocking path spent 0.8s per agent here.
        assert elapsed < 0.3
    finally:
        _cleanup_ws(cat.id)


@pytest.mark.asyncio
async def test_dispatch_describes_the_tick_the_answer_will_land_on():
    """`can_act` refers to the next tick, because that is when the reply executes."""
    sim, cat = _sim_with_ws_cat()
    from app.game.ws_manager import ws_agent_manager

    sock = ws_agent_manager.active_connections[cat.id]
    try:
        cat.action_cooldown = 0
        await sim.scheduler._dispatch_observations()
        assert sock.sent[-1]["payload"]["self"]["can_act"] is True

        cat.action_cooldown = 2
        await sim.scheduler._dispatch_observations()
        assert sock.sent[-1]["payload"]["self"]["can_act"] is False
    finally:
        _cleanup_ws(cat.id)


# -- provenance counters ------------------------------------------------


def test_model_control_is_null_when_no_agent_ever_answered():
    """A heuristic-only cat has no model share, which is not the same as zero."""
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 0}))
    row = sim.get_boxscore()["cats"][0]
    assert row["model_control_pct"] is None


def test_model_control_counts_only_ticks_a_model_could_have_answered():
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 0}))
    cat = next(
        e for e in sim.entity_manager.get_alive_entities() if e.type == EntityType.CAT
    )
    sim.stats.record_decision_source(cat, COLLECT_FRESH, engine=MODEL_ENGINE)
    sim.stats.record_decision_source(cat, COLLECT_FRESH, engine=MODEL_ENGINE)
    sim.stats.record_decision_source(cat, COLLECT_MISSING)
    sim.stats.record_decision_source(cat, COLLECT_STALE)

    row = next(c for c in sim.get_boxscore()["cats"] if c["id"] == cat.id)

    assert row["decisions_by_model"] == 2
    assert row["decisions_by_fallback"] == 2
    assert row["decisions_stale"] == 1
    assert row["model_control_pct"] == 0.5


def test_a_prompt_reflex_answer_is_not_credited_to_the_model():
    """The agent answers instantly either way; only the engine tells them apart.

    This is the trap the counter exists to avoid. `act_async(decoupled=True)`
    serves a completed Gemini deliberation if one is ready and otherwise answers
    from its own System-1 reflex, and both reach the server within milliseconds.
    Timing cannot distinguish them, so a model that never finishes a deliberation
    would otherwise show 100% control.
    """
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 0}))
    cat = next(
        e for e in sim.entity_manager.get_alive_entities() if e.type == EntityType.CAT
    )
    sim.stats.record_decision_source(cat, COLLECT_FRESH, engine=MODEL_ENGINE)
    sim.stats.record_decision_source(cat, COLLECT_FRESH, engine="heuristic_reflex")
    sim.stats.record_decision_source(cat, COLLECT_FRESH, engine="heuristic_reflex")
    sim.stats.record_decision_source(cat, COLLECT_FRESH, engine="heuristic_reflex")

    row = next(c for c in sim.get_boxscore()["cats"] if c["id"] == cat.id)

    assert row["decisions_by_model"] == 1
    assert row["decisions_by_agent_reflex"] == 3
    assert row["decisions_by_fallback"] == 0
    assert row["model_control_pct"] == 0.25


def test_an_agent_that_declares_no_engine_is_taken_at_its_word():
    """Third-party agents predate the field; muting them would be worse than trusting them."""
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 0}))
    cat = next(
        e for e in sim.entity_manager.get_alive_entities() if e.type == EntityType.CAT
    )
    sim.stats.record_decision_source(cat, COLLECT_FRESH, engine=None)

    row = next(c for c in sim.get_boxscore()["cats"] if c["id"] == cat.id)

    assert row["decisions_by_model"] == 1
    assert row["decisions_by_agent_reflex"] == 0


def test_provenance_counters_rewind_with_the_board():
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 0}))
    cat = next(
        e for e in sim.entity_manager.get_alive_entities() if e.type == EntityType.CAT
    )
    stats = sim.stats.get(cat.id)
    sim.stats.record_decision_source(cat, COLLECT_FRESH)
    snap = stats.snapshot()
    sim.stats.record_decision_source(cat, COLLECT_STALE)
    assert stats.decisions_by_fallback == 1

    stats.restore(snap)

    assert stats.decisions_by_model == 1
    assert stats.decisions_by_fallback == 0
    assert stats.decisions_stale == 0
