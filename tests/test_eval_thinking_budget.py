"""The evaluator's thinking budget: the second dial, deliberately separate.

The actor's budget explains a box score. This one explains the standing orders
the actor was playing under, which is a different claim about a run and has to
be recoverable independently.

The properties pinned here are the ones that would let a run be misread:

  * moving one agent's dial never moves the other's,
  * a value the model would reject never silently becomes a different one,
  * the archive records which agent changed and when,
  * and an evaluation that overruns is visible as a timeout rather than
    quietly presenting offline filler as coaching.
"""

import asyncio

import pytest
from starlette.testclient import TestClient

from actors.cat import actor, store
from actors.cat.api import app, publish_run_config, state
from actors.cat.subagents import eval as ev


@pytest.fixture(autouse=True)
def restore_eval_thinking_settings():
    """Every test leaves both agents exactly as it found them.

    `eval_agent` is a module-level singleton shared by the whole suite, and
    `test_game_archive` asserts against the live object, so a test that raised
    the budget and walked away would change an unrelated assertion.
    """
    original_budget = ev.get_eval_thinking_budget()
    original_timeout = ev.get_eval_timeout()
    original_changes = list(state.eval_thinking_budget_changes)
    original_telemetry = dict(ev._last_eval_telemetry)
    state.eval_thinking_budget_changes.clear()
    yield
    ev.set_eval_thinking_budget(original_budget)
    ev.set_eval_timeout(original_timeout)
    state.eval_thinking_budget_changes[:] = original_changes
    ev._last_eval_telemetry.clear()
    ev._last_eval_telemetry.update(original_telemetry)


# --- the default ---------------------------------------------------------


def test_the_coach_starts_with_thinking_disabled():
    """Preserves the configuration every coaching note in the repo was written
    under. A new default would silently invalidate the archive as a baseline."""
    assert ev.get_eval_thinking_budget() == ev.THINKING_BUDGET_OFF
    assert ev.get_eval_timeout() == ev.EVAL_TIMEOUT_SECONDS


def test_the_environment_can_seed_the_coach_budget(monkeypatch):
    monkeypatch.setenv(ev.EVAL_THINKING_BUDGET_ENV, "2048")
    seeded = ev._initial_from_env(
        ev.EVAL_THINKING_BUDGET_ENV,
        ev.coerce_eval_thinking_budget,
        ev.THINKING_BUDGET_OFF,
        ev.describe_thinking_budget,
    )
    assert seeded == 2048


def test_a_malformed_environment_value_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv(ev.EVAL_THINKING_BUDGET_ENV, "lots")
    seeded = ev._initial_from_env(
        ev.EVAL_THINKING_BUDGET_ENV,
        ev.coerce_eval_thinking_budget,
        ev.THINKING_BUDGET_OFF,
        ev.describe_thinking_budget,
    )
    assert seeded == ev.THINKING_BUDGET_OFF


def test_the_coach_uses_its_own_env_var_not_the_actors():
    """A shared variable would move both agents at once and make every
    subsequent run a two-variable experiment."""
    assert ev.EVAL_THINKING_BUDGET_ENV != actor.DEFAULT_THINKING_BUDGET_ENV


# --- the two dials are independent ---------------------------------------


def test_moving_the_coach_does_not_move_the_actor():
    before = actor.get_thinking_budget()
    ev.set_eval_thinking_budget(4096)
    assert actor.get_thinking_budget() == before


def test_moving_the_actor_does_not_move_the_coach():
    before = ev.get_eval_thinking_budget()
    original = actor.get_thinking_budget()
    try:
        actor.set_thinking_budget(4096)
        assert ev.get_eval_thinking_budget() == before
    finally:
        actor.set_thinking_budget(original)


def test_the_two_agents_hold_distinct_thinking_configs():
    """Not merely equal values -- distinct objects. If they shared one config,
    setting either would set both, and the independence above would pass by
    coincidence until the first time someone used it."""
    ev.set_eval_thinking_budget(512)
    actor_cfg = actor.adk_agent.generate_content_config.thinking_config
    eval_cfg = ev.eval_agent.generate_content_config.thinking_config
    assert actor_cfg is not eval_cfg


# --- what the model will accept ------------------------------------------


@pytest.mark.parametrize("value", [0, -1, 1, 512, 24576])
def test_legal_coach_budgets_are_accepted(value):
    assert ev.coerce_eval_thinking_budget(value) == value


@pytest.mark.parametrize("value", [-2, 24577, 100000])
def test_out_of_range_coach_budgets_raise_rather_than_clamp(value):
    with pytest.raises(ValueError):
        ev.coerce_eval_thinking_budget(value)


def test_the_bounds_are_derived_from_the_coachs_model_not_the_actors():
    """The shared table is consulted per model. Today they match; the point is
    that they are not assumed to."""
    expected = actor.thinking_bounds_for(ev.EVAL_MODEL)
    assert (
        ev.EVAL_THINKING_MIN,
        ev.EVAL_THINKING_MAX,
        ev.EVAL_THINKING_CAN_BE_DISABLED,
    ) == expected


def test_a_rejected_coach_budget_names_the_coachs_model():
    with pytest.raises(ValueError) as exc:
        ev.coerce_eval_thinking_budget(999999)
    assert ev.EVAL_MODEL in str(exc.value)


# --- the timeout it is coupled to ----------------------------------------


@pytest.mark.parametrize("value", [30, 60, 900])
def test_legal_eval_timeouts_are_accepted(value):
    assert ev.coerce_eval_timeout(value) == float(value)


@pytest.mark.parametrize("value", [0, 29, 901])
def test_out_of_range_eval_timeouts_raise(value):
    with pytest.raises(ValueError):
        ev.coerce_eval_timeout(value)


def test_the_timeout_floor_keeps_the_boxscore_call_a_small_fraction():
    """Structural, not aspirational. At any legal timeout the boxscore fetch
    can consume at most a tenth of the evaluation, so advice is never published
    off a measurement that never arrived."""
    assert ev.EVAL_TIMEOUT_MIN_SEC >= ev.BOXSCORE_TIMEOUT_SECONDS * 10


def test_the_coach_baseline_latency_exceeds_the_actors():
    """The coach uploads a whole transcript and makes a tool call before it
    writes a word. Sharing the actor's 1.3s figure overstated its headroom."""
    assert ev.EVAL_BASELINE_LATENCY_SEC > actor._BASELINE_LATENCY_SEC


def test_the_timeout_bounds_how_much_thinking_is_affordable():
    ev.set_eval_timeout(60)
    narrow = ev.eval_affordable_thinking_tokens()
    ev.set_eval_timeout(300)
    assert ev.eval_affordable_thinking_tokens() > narrow


def test_a_budget_that_cannot_land_inside_the_timeout_warns():
    ev.set_eval_timeout(30)
    warning = ev.eval_thinking_warning(24576)
    assert warning is not None
    # The warning has to carry the consequence, not just the objection: this
    # failure mode overwrites good standing orders with generated filler.
    assert "filler" in warning
    assert "standing orders" in warning


def test_an_affordable_coach_budget_does_not_warn():
    ev.set_eval_timeout(600)
    assert ev.eval_thinking_warning(1024) is None


def test_disabled_coach_thinking_never_warns():
    ev.set_eval_timeout(ev.EVAL_TIMEOUT_MIN_SEC)
    assert ev.eval_thinking_warning(ev.THINKING_BUDGET_OFF) is None


def test_the_suggested_timeout_is_actually_enough():
    ev.set_eval_timeout(30)
    suggested = ev.suggested_eval_timeout(8192)
    assert suggested is not None
    assert ev.eval_affordable_thinking_tokens(suggested) >= 8192


# --- the endpoint --------------------------------------------------------


def test_the_endpoint_reports_the_bounds_the_coachs_model_has():
    with TestClient(app) as client:
        body = client.get("/api/agent/evaluator/thinking-budget").json()
    assert body["model"] == ev.EVAL_MODEL
    assert body["min"] == ev.EVAL_THINKING_MIN
    assert body["max"] == ev.EVAL_THINKING_MAX
    assert body["timeout_min_sec"] == ev.EVAL_TIMEOUT_MIN_SEC
    assert body["timeout_max_sec"] == ev.EVAL_TIMEOUT_MAX_SEC


def test_the_endpoint_applies_a_coach_budget():
    with TestClient(app) as client:
        body = client.post(
            "/api/agent/evaluator/thinking-budget",
            json={"thinking_budget": 2048, "eval_timeout_sec": 120},
        ).json()
    assert body["budget"] == 2048
    assert body["timeout_sec"] == 120.0
    assert ev.get_eval_thinking_budget() == 2048
    assert ev.get_eval_timeout() == 120.0


def test_a_rejected_coach_budget_changes_nothing_and_says_why():
    ev.set_eval_thinking_budget(1024)
    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/evaluator/thinking-budget", json={"thinking_budget": 999999}
        )
    assert resp.status_code == 400
    assert "out of range" in resp.json()["detail"]
    # The live value rides along so the UI snaps back to the truth.
    assert resp.json()["budget"] == 1024
    assert ev.get_eval_thinking_budget() == 1024


def test_a_bad_timeout_does_not_half_apply_the_request():
    """Both values are validated before either is applied. A partial apply would
    leave the coach in a state nobody asked for and nobody is looking at."""
    ev.set_eval_thinking_budget(1024)
    ev.set_eval_timeout(60)
    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/evaluator/thinking-budget",
            json={"thinking_budget": 4096, "eval_timeout_sec": 1},
        )
    assert resp.status_code == 400
    assert ev.get_eval_thinking_budget() == 1024
    assert ev.get_eval_timeout() == 60.0


def test_omitting_the_timeout_leaves_it_unchanged():
    ev.set_eval_timeout(300)
    with TestClient(app) as client:
        body = client.post(
            "/api/agent/evaluator/thinking-budget", json={"thinking_budget": 512}
        ).json()
    assert body["timeout_sec"] == 300.0


def test_the_coach_budget_rides_the_poll_the_workbench_already_makes():
    """The workbench polls /api/agent/state and nothing else.

    This test previously asserted /api/agent/evaluator, which reads like the
    obvious home for the coach's status and is in fact polled by no one. It
    passed while the slider was completely inert: the control never received
    its bounds, so it kept the placeholder ladder from the markup and posted an
    undefined budget as soon as it was dragged. Asserting the endpoint the
    feature is *named* after is not the same as asserting the endpoint the
    feature is *served* by.
    """
    ev.set_eval_thinking_budget(256)
    with TestClient(app) as client:
        body = client.get("/api/agent/state").json()
    thinking = body["evaluator"]["thinking"]
    assert thinking["budget"] == 256
    assert "last_run" in thinking


def test_the_poll_carries_every_bound_the_slider_needs():
    """The ladder is built from these four. Miss one and the control is inert.

    Spelled out field by field rather than compared against a snapshot: the
    failure mode is a single missing key, and the point of the test is to name
    which one.
    """
    with TestClient(app) as client:
        thinking = client.get("/api/agent/state").json()["evaluator"]["thinking"]
    for key in ("off", "auto", "min", "max", "can_disable"):
        assert key in thinking, f"the slider cannot build its ladder without {key!r}"
    assert thinking["max"] > thinking["min"]


def test_both_evaluator_payloads_say_the_same_thing():
    """Guards the duplication that caused the bug in the first place.

    The two endpoints were separate dict literals describing one box, and they
    drifted the moment a field was added to only one of them. They are now
    built by a single helper; this fails if anyone splits them again.
    """
    ev.set_eval_thinking_budget(512)
    with TestClient(app) as client:
        standalone = client.get("/api/agent/evaluator").json()
        embedded = client.get("/api/agent/state").json()["evaluator"]
    assert standalone.keys() == embedded.keys()
    assert standalone["thinking"]["budget"] == embedded["thinking"]["budget"] == 512


def test_the_actor_endpoint_is_untouched_by_the_coach_endpoint():
    original = actor.get_thinking_budget()
    try:
        with TestClient(app) as client:
            client.post(
                "/api/agent/evaluator/thinking-budget", json={"thinking_budget": 8192}
            )
            body = client.get("/api/agent/thinking-budget").json()
        assert body["budget"] == original
    finally:
        actor.set_thinking_budget(original)


# --- what the archive ends up saying --------------------------------------


def test_a_mid_game_coach_change_is_recorded_against_its_tick():
    state.active_tick = 412
    with TestClient(app) as client:
        client.post(
            "/api/agent/evaluator/thinking-budget", json={"thinking_budget": 4096}
        )
    assert len(state.eval_thinking_budget_changes) == 1
    entry = state.eval_thinking_budget_changes[0]
    assert entry["at_tick"] == 412
    assert entry["to"] == 4096


def test_the_coachs_change_log_is_kept_apart_from_the_actors():
    """Merged, every reader would have to filter before either 'why was the box
    score like that' or 'why was the advice like that' could be answered."""
    original_actor_log = list(state.thinking_budget_changes)
    state.thinking_budget_changes.clear()
    try:
        with TestClient(app) as client:
            client.post(
                "/api/agent/evaluator/thinking-budget", json={"thinking_budget": 512}
            )
        assert len(state.eval_thinking_budget_changes) == 1
        assert state.thinking_budget_changes == []
    finally:
        state.thinking_budget_changes[:] = original_actor_log


def test_setting_the_same_value_twice_logs_one_change():
    with TestClient(app) as client:
        client.post(
            "/api/agent/evaluator/thinking-budget", json={"thinking_budget": 1024}
        )
        client.post(
            "/api/agent/evaluator/thinking-budget", json={"thinking_budget": 1024}
        )
    assert len(state.eval_thinking_budget_changes) == 1


def test_the_run_config_records_the_coachs_settings(tmp_path, monkeypatch):
    """The archive is copied when the game ends, long before the process does,
    so a coaching run has to be attributable to the budget that produced it."""
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))
    import importlib
    import json
    importlib.reload(store)
    # `api` captured the module object at import, so point it at the reloaded one.
    import actors.cat.api as api_module
    monkeypatch.setattr(api_module, "store", store)
    try:
        ev.set_eval_thinking_budget(2048)
        ev.set_eval_timeout(180)
        publish_run_config()

        written = json.loads(store.RUN_CONFIG_PATH.read_text())
        assert written["evaluation"]["timeout_sec"] == 180.0
        assert "eval_thinking_budget_changes" in written

        coach = next(a for a in written["agents"] if a["name"] == "cat_evaluator")
        assert coach["thinking_budget"] == 2048
        assert coach["thinking_budget_label"] == "2,048 tokens"

        # And a later change rewrites it, rather than leaving the file claiming
        # a budget that stopped being true an hour ago.
        client = TestClient(app)
        client.post(
            "/api/agent/evaluator/thinking-budget", json={"thinking_budget": 512}
        )
        second = json.loads(store.RUN_CONFIG_PATH.read_text())
        coach = next(a for a in second["agents"] if a["name"] == "cat_evaluator")
        assert coach["thinking_budget"] == 512
        assert second["eval_thinking_budget_changes"][-1]["to"] == 512
    finally:
        monkeypatch.undo()
        importlib.reload(store)


def test_the_archive_summary_follows_the_live_coach_agent():
    """Introspected off the object rather than restated, so it cannot keep
    reporting 0 for months after someone raised it."""
    ev.set_eval_thinking_budget(-1)
    summary = actor.summarize_agent(ev.eval_agent)
    assert summary["thinking_budget"] == -1
    assert summary["thinking_budget_label"] == "auto (model decides)"


# --- telemetry: did the thinking actually happen --------------------------


def test_an_evaluation_records_what_it_spent():
    """Without this the dial is unfalsifiable: the coach runs once per ~100
    ticks, so there is no stream of per-decision records to infer spend from."""
    ev.set_eval_thinking_budget(1024)
    # Under pytest `evaluate` short-circuits to the offline fallback before it
    # would dial out, so this exercises the bookkeeping, not the network.
    notes, source = asyncio.run(ev.evaluate([{"tick": 1, "x": 1, "y": 1}]))
    assert source == "offline_fallback"
    telemetry = ev.get_last_eval_telemetry()
    # The offline path returns before the model call, so nothing is stamped --
    # which is correct: reporting a spend for a run that never called the model
    # would be inventing a measurement.
    assert set(telemetry) == {
        "thinking_budget",
        "thoughts_token_count",
        "latency_ms",
        "timed_out",
    }


def test_telemetry_is_copied_so_callers_cannot_corrupt_it():
    snapshot = ev.get_last_eval_telemetry()
    snapshot["thoughts_token_count"] = 999999
    assert ev.get_last_eval_telemetry()["thoughts_token_count"] != 999999


def test_unreported_thinking_is_distinct_from_no_thinking():
    """None means the response carried no usage metadata; 0 means it thought
    nothing. Collapsing them would turn a missing measurement into a finding."""
    assert ev._last_eval_telemetry["thoughts_token_count"] is None
