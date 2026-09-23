"""Regression tests for the transcript-wipe and engine-mislabelling bugs.

Four distinct defects are covered here, all of which were observed live:

1.  A poll whose server tick LAGS the workbench's own tick wiped the entire
    transcript. `sync_live_observation` advances `upcoming_tick` past the server
    while the game is paused, then read that self-inflicted gap as a "rewind".

2.  `reset_reflexion_state()` zeroed the evaluation clock, so `tick - 0 >=
    interval` was already satisfied and an evaluation fired immediately against a
    transcript holding a single record.

3.  Any ADK exception was classified as an auth failure and poisoned a 5-second
    credential cache, so a timeout presented as "Fallback (Auth Error)".

4.  Decision source was re-derived from LIVE auth state, so healthy credentials
    retroactively relabelled past heuristic ticks as "ADK Gemini".
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from actors.cat import store
from actors.cat.actor import is_auth_error
from actors.cat.api import app, state, reset_reflexion_state, act_async
from actors.models import Observation


def _obs(tick, **overrides):
    payload = {
        "tick": tick,
        "round": 1,
        "self": {
            "id": "cat-1",
            "type": "cat",
            "position": {"x": 5, "y": 5},
            "facing": "east",
            "action_cooldown": 0,
        },
        "visible_entities": [],
        "legal_actions": ["stay", "north", "east"],
        "capabilities": {"attack_range": 2, "vision_radius": 4},
        "score": 0,
        "scoreboard": {"cat-1": 0},
    }
    payload.update(overrides)
    return payload


def _server_payload(tick, sim_id="sim-1"):
    return {
        "simulation_id": sim_id,
        "tick": tick,
        "round": 1,
        "self": {
            "id": "cat-1",
            "type": "cat",
            "position": {"x": 5, "y": 5},
            "facing": "east",
            "action_cooldown": 0,
        },
        "visible_entities": [],
        "legal_actions": ["stay", "north", "east"],
        "capabilities": {"attack_range": 2, "vision_radius": 4},
        "score": 0,
        "scoreboard": {"cat-1": 0},
    }


def _poll_with_server_tick(client, tick, sim_id="sim-1"):
    """Drives one `/api/agent/state?sync=1` with a stubbed game server."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: _server_payload(tick, sim_id)
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        return client.get("/api/agent/state?sync=1")


@pytest.fixture(autouse=True)
def _clean_state():
    """Each test starts from a clean transcript and a clean tick history."""
    state.history.clear()
    state.simulation_id = None
    state.last_server_tick = None
    state.last_evaluated_tick = None
    state.total_ticks_processed = 0
    state.eval_pending = False
    state.last_engine_error = None
    state.last_fallback_reason = None
    store.reset_transcript()
    store.reset_coaching_notes()
    yield
    store.reset_transcript()
    store.reset_coaching_notes()


# --------------------------------------------------------------------------
# 1. Lagging server tick must not be mistaken for a rewind
# --------------------------------------------------------------------------

def test_lagging_server_tick_does_not_wipe_transcript():
    """The workbench polls faster than the game ticks; that is NOT a rewind.

    This is the exact scenario measured live (`game_tick=1081`,
    `workbench_active_tick=1082`) that collapsed a 687-record transcript.
    """
    client = TestClient(app)
    for tick in range(1, 41):
        client.post("/act", json=_obs(tick))

    assert store.transcript_tick_count() == 40
    assert state.history[-1]["tick"] == 40

    # Server is one tick BEHIND the workbench. Previously: instant wipe.
    _poll_with_server_tick(client, 39)

    # `sync_live_observation` wraps its whole body in `except Exception: pass`.
    # Without this guard the test would pass if the poll merely crashed early,
    # which is exactly how an invalid mutation slipped through once.
    assert state.last_server_tick == 39, (
        "the poll did not run to completion, so the 'no wipe' result below proves "
        "nothing -- sync_live_observation swallowed an exception"
    )

    assert store.transcript_tick_count() == 40, (
        "a lagging server tick wiped the transcript; the poller misread its own "
        "look-ahead as a game rewind"
    )


def test_repeated_lagging_polls_do_not_wipe_transcript():
    """The browser polls continuously, so the misread must not accumulate."""
    client = TestClient(app)
    for tick in range(1, 21):
        client.post("/act", json=_obs(tick))
    assert store.transcript_tick_count() == 20

    for _ in range(10):
        _poll_with_server_tick(client, 19)

    assert store.transcript_tick_count() >= 20


# --------------------------------------------------------------------------
# 2. A genuine rewind MUST still reset
# --------------------------------------------------------------------------

def test_genuine_server_rewind_still_resets():
    """Guards against 'fixing' the wipe by disabling rewind detection entirely."""
    client = TestClient(app)
    for tick in range(1, 41):
        client.post("/act", json=_obs(tick))
    assert store.transcript_tick_count() == 40

    # Establish the server's tick baseline at 40...
    _poll_with_server_tick(client, 40)
    assert store.transcript_tick_count() == 40

    # ...then the GAME genuinely restarts and reports a much lower tick.
    _poll_with_server_tick(client, 3)

    assert store.transcript_tick_count() < 40, (
        "a real server-side rewind must still clear the transcript, otherwise the "
        "evaluator reads across two unrelated games"
    )


def test_simulation_id_change_resets():
    """A new simulation must never inherit the previous game's transcript."""
    client = TestClient(app)
    for tick in range(1, 31):
        client.post("/act", json=_obs(tick))
    _poll_with_server_tick(client, 30, sim_id="sim-1")
    assert store.transcript_tick_count() >= 30

    _poll_with_server_tick(client, 31, sim_id="sim-2")

    assert store.transcript_tick_count() < 30


def test_reset_reflexion_state_wipes_coach_memories():
    """Coach tactical memories are scoped to a single game and must not leak into the next."""
    store.append_coach_memory("Sector 4 ambush setup", category="tactical")
    assert len(store.read_coach_memories()) == 1

    reset_reflexion_state(0)

    assert len(store.read_coach_memories()) == 0


# --------------------------------------------------------------------------
# 3. Reset rebases the evaluation clock
# --------------------------------------------------------------------------

def test_reset_rebases_eval_clock_to_current_tick():
    reset_reflexion_state(1068)
    assert state.last_eval_dispatch_tick == 1068, (
        "zeroing the clock made `tick - 0 >= interval` instantly true, firing an "
        "evaluation against a one-record transcript"
    )


def test_reset_without_tick_still_defaults_to_zero():
    """Back-compat for callers with no tick context (e.g. cold start)."""
    reset_reflexion_state()
    assert state.last_eval_dispatch_tick == 0


def test_no_evaluation_fires_immediately_after_late_game_reset():
    """The amplifier: a reset at tick 1068 must not evaluate a 1-tick transcript."""
    client = TestClient(app)
    state.eval_enabled = True
    state.eval_interval = 100

    reset_reflexion_state(1068)
    state.eval_pending = False

    client.post("/act", json=_obs(1069))

    assert not state.eval_pending, (
        "an evaluation was queued one tick after a reset; the cat's standing "
        "orders would be derived from a single tick"
    )
    assert store.transcript_tick_count() == 1


# --------------------------------------------------------------------------
# 4. Auth-error classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "max output tokens exceeded",
    "Request timed out after 8s",
    "503 Service Unavailable",
    "model returned no parseable decision",
    "DeadlineExceeded",
])
def test_transient_errors_are_not_auth_errors(msg):
    """A transient failure must not poison the credential cache.

    The old test included a bare `"token" in msg.lower()`, so
    'max output tokens exceeded' was classified as an expired credential and the
    UI showed 'Fallback (Auth Error)' for a perfectly healthy login.
    """
    assert is_auth_error(msg) is False, f"{msg!r} misclassified as an auth error"


@pytest.mark.parametrize("msg", [
    "RefreshError: invalid_grant",
    "Reauthentication is needed. Please run `gcloud auth application-default login`",
    "401 Unauthorized",
    "403 Permission denied on resource project",
    "Could not automatically determine credentials",
    "API key not valid. Please pass a valid API key.",
])
def test_genuine_credential_failures_are_auth_errors(msg):
    assert is_auth_error(msg) is True, f"{msg!r} should be treated as an auth error"


# --------------------------------------------------------------------------
# 5. Source labelling must reflect what actually happened
# --------------------------------------------------------------------------

def test_state_endpoint_does_not_relabel_source_when_auth_healthy():
    """Healthy credentials say nothing about how the last tick was decided.

    This is the server-side twin of the JS `|| hasAuth` bug: `get_agent_state`
    force-set `last_engine_source = "adk_gemini"` on every poll, so history that
    was genuinely heuristic started claiming it came from the model.
    """
    client = TestClient(app)
    state.last_engine_source = "heuristic_fallback"
    state.last_fallback_reason = "model chose 'attack', which is not legal this tick"

    with patch("actors.cat.api.check_gemini_auth_health",
               return_value={"authenticated": True, "mode": "vertex_ai", "status": "ok"}):
        data = client.get("/api/agent/state").json()

    assert data["last_engine_source"] == "heuristic_fallback", (
        "healthy auth retroactively relabelled a heuristic decision as Gemini"
    )
    assert data["last_fallback_reason"] == (
        "model chose 'attack', which is not legal this tick"
    )


def test_state_endpoint_exposes_fallback_reason_field():
    client = TestClient(app)
    data = client.get("/api/agent/state").json()
    assert "last_fallback_reason" in data


def test_act_records_fallback_reason_on_response():
    """The reason must travel with the tick, not live in mutable global state.

    No credentials AND no error string is the "never configured" case, which is
    the plain `heuristic` branch rather than a fallback from a failure.
    """
    client = TestClient(app)
    with patch("actors.cat.api.check_gemini_auth_health",
               return_value={"authenticated": False, "mode": "none",
                             "status": "missing", "error": None}), \
         patch("actors.cat.api.has_gemini_credentials", return_value=False):
        client.post("/act", json=_obs(1))

    assert state.history, "no history recorded"
    item = state.history[-1]
    assert item["response"]["source"] == "heuristic"
    assert item["response"]["fallback_reason"] == "no Gemini credentials configured"


def test_unauthenticated_tick_is_labelled_fallback_not_gemini():
    client = TestClient(app)
    with patch("actors.cat.api.check_gemini_auth_health",
               return_value={"authenticated": False, "mode": "vertex_ai",
                             "status": "expired", "error": "RefreshError: invalid_grant"}), \
         patch("actors.cat.api.has_gemini_credentials", return_value=True):
        client.post("/act", json=_obs(1))

    item = state.history[-1]
    assert item["response"]["source"] == "heuristic_fallback"
    assert item["response"]["fallback_reason"] == "RefreshError: invalid_grant"


def test_recorded_source_survives_auth_recovery():
    """The end-to-end statement of the user-visible bug.

    A tick decided by the heuristic while auth was down must STILL read as a
    fallback after credentials recover. Previously ticks flipped from
    'Fallback (Auth Error)' to 'ADK Gemini' between two renders.
    """
    client = TestClient(app)

    # Tick 1 decided while auth is down.
    with patch("actors.cat.api.check_gemini_auth_health",
               return_value={"authenticated": False, "mode": "vertex_ai",
                             "status": "expired", "error": "RefreshError: invalid_grant"}), \
         patch("actors.cat.api.has_gemini_credentials", return_value=True):
        client.post("/act", json=_obs(1))

    recorded = state.history[-1]["response"]["source"]
    assert recorded == "heuristic_fallback"

    # Credentials recover; the browser polls again.
    with patch("actors.cat.api.check_gemini_auth_health",
               return_value={"authenticated": True, "mode": "vertex_ai", "status": "ok"}):
        data = client.get("/api/agent/state").json()

    tick_one = [h for h in data["history"] if h["tick"] == 1]
    assert tick_one, "tick 1 missing from history"
    assert tick_one[0]["response"]["source"] == "heuristic_fallback", (
        "tick 1 was retroactively relabelled after auth recovered"
    )


def test_verify_auth_does_not_claim_gemini_source():
    """Reauthenticating clears the error but must not invent a Gemini decision."""
    client = TestClient(app)
    state.last_engine_source = "heuristic_fallback"
    state.last_engine_error = "RefreshError: invalid_grant"

    with patch("actors.cat.api.check_gemini_auth_health",
               return_value={"authenticated": True, "mode": "vertex_ai", "status": "ok"}):
        client.post("/api/agent/auth/verify")

    assert state.last_engine_error is None, "resolved credential error should clear"
    assert state.last_engine_source == "heuristic_fallback", (
        "reauth must not relabel the last decision as Gemini-sourced"
    )


def test_decoupled_reflex_tick_is_not_credited_to_gemini():
    """The WebSocket fast path is a heuristic and must say so.

    With `decoupled=True` and no completed background deliberation, the decision
    comes from the local System-1 reflex. This branch used to set
    `engine = "adk_gemini"`, which is why the workbench reported Gemini for ticks
    the model never saw.
    """
    state.latest_deliberated_decision = None
    state.is_deliberating = False
    state.deliberation_task = None

    obs = Observation(**_obs(1))

    async def _drive():
        with patch("actors.cat.api.check_gemini_auth_health",
                   return_value={"authenticated": True, "mode": "vertex_ai", "status": "ok"}), \
             patch("actors.cat.api._run_background_deliberation", new=AsyncMock()):
            return await act_async(obs, decoupled=True)

    asyncio.run(_drive())

    assert state.last_engine_source == "heuristic_reflex", (
        f"reflex tick credited to {state.last_engine_source!r}; the model had not "
        f"returned a decision"
    )
    item = state.history[-1]
    assert item["response"]["source"] == "heuristic_reflex"
    assert item["response"]["fallback_reason"], (
        "a non-Gemini tick must record why the model did not decide it"
    )


def test_background_deliberation_does_not_claim_the_last_decision():
    """Found by live verification, not by the unit suite.

    The workbench reported `last_engine_source='adk_gemini'` while the only
    recorded history row was `heuristic_reflex`. Cause: a completed background
    deliberation overwrote the source. It prepares a decision for a FUTURE tick
    and must not claim authorship of the one already executed.
    """
    from actors.cat.api import _run_background_deliberation
    from actors.cat.actor import CatTacticalDecision

    state.last_engine_source = "heuristic_reflex"
    decision = CatTacticalDecision(
        action="east", thought="go east", strategy="ROAMING", target_mouse_id=None
    )

    async def _drive():
        with patch("actors.cat.api.model_query_adk_gemini",
                   new=AsyncMock(return_value=(decision, "adk_gemini", None))):
            await _run_background_deliberation(Observation(**_obs(2)))

    asyncio.run(_drive())

    assert state.last_engine_source == "heuristic_reflex", (
        f"background deliberation relabelled the last decision as "
        f"{state.last_engine_source!r}; it did not decide that tick"
    )
    # It must still hand the decision forward for the next tick to consume.
    assert state.latest_deliberated_decision is decision


def test_failed_background_deliberation_does_not_relabel_either():
    from actors.cat.api import _run_background_deliberation

    state.last_engine_source = "adk_gemini"

    async def _drive():
        with patch("actors.cat.api.model_query_adk_gemini",
                   new=AsyncMock(side_effect=RuntimeError("connection reset"))):
            await _run_background_deliberation(Observation(**_obs(3)))

    asyncio.run(_drive())

    assert state.last_engine_source == "adk_gemini", (
        "a failed background call does not change how the previous tick was decided"
    )
    assert state.last_fallback_reason == "connection reset"
