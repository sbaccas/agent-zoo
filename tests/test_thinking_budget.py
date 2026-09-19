"""The actor's thinking budget: the dial an A/B run turns.

These tests are about trustworthiness rather than plumbing. The budget is the
independent variable of a prompt experiment, so the properties worth pinning
are the ones that would let a run be misread afterwards:

  * the value the UI shows is the value the model gets,
  * a value the model would reject never silently becomes a different one,
  * and the archive records what actually ran, including a mid-game change.
"""

import pytest
from starlette.testclient import TestClient

from actors.cat import store
from actors.cat.api import app, publish_run_config, state
from actors.cat import actor


@pytest.fixture(autouse=True)
def restore_thinking_settings():
    """Every test leaves the actor exactly as it found it.

    `adk_agent` is a module-level singleton shared by the whole suite, so a test
    that raised the budget and walked away would change how unrelated tests see
    the agent -- including `test_game_archive`'s assertions about the live
    object.
    """
    original_budget = actor.get_thinking_budget()
    original_timeout = actor.get_deliberation_timeout()
    original_changes = list(state.thinking_budget_changes)
    state.thinking_budget_changes.clear()
    yield
    actor.set_thinking_budget(original_budget)
    actor.set_deliberation_timeout(original_timeout)
    state.thinking_budget_changes[:] = original_changes


# --- the default ---------------------------------------------------------


def test_the_budget_starts_on_the_documented_default():
    """The literal is pinned on purpose, not read from the constant.

    Asserting `== actor.THINKING_BUDGET_DEFAULT` here would pass no matter what
    anyone set it to, which defeats the point. The default was 0 for the whole
    history of the repo and CatTacticalDecision's thought-before-action field
    order was measured under that 0, so moving it is a change to the conditions
    of a documented result and should fail here first.

    0 -> 256 -> 512. The second move accompanied widening the cat's viewport to
    13x11: more entities per observation means more per-mouse frame rotations,
    which is what the scratchpad is spent on when aim assist is off.
    """
    assert actor.THINKING_BUDGET_DEFAULT == 512
    assert actor.get_thinking_budget() == 512


def test_the_environment_can_seed_the_budget(monkeypatch):
    monkeypatch.setenv("CAT_THINKING_BUDGET", "2048")
    assert actor._initial_thinking_budget() == 2048


def test_a_malformed_environment_value_falls_back_rather_than_crashing(monkeypatch):
    """A typo in a tuning variable must not stop the workbench booting."""
    monkeypatch.setenv("CAT_THINKING_BUDGET", "lots")
    assert actor._initial_thinking_budget() == actor.THINKING_BUDGET_DEFAULT


# --- what the model will accept ------------------------------------------


@pytest.mark.parametrize("value", [0, -1, 1, 512, 24576])
def test_legal_budgets_are_accepted(value):
    assert actor.coerce_thinking_budget(value) == value


@pytest.mark.parametrize("value", [-2, 24577, 100000])
def test_out_of_range_budgets_raise_rather_than_clamp(value):
    """Clamping would let the archive record a budget nobody chose."""
    with pytest.raises(ValueError):
        actor.coerce_thinking_budget(value)


def test_a_non_numeric_budget_raises():
    with pytest.raises(ValueError):
        actor.coerce_thinking_budget("some")


# --- the value reaching the model ----------------------------------------


def test_setting_the_budget_changes_what_the_model_is_sent():
    """The point of the whole feature: ADK reads this config per request."""
    actor.set_thinking_budget(4096)

    thinking = actor.adk_agent.generate_content_config.thinking_config
    assert thinking.thinking_budget == 4096
    assert actor.get_thinking_budget() == 4096


def test_changing_the_budget_does_not_mutate_the_config_an_inflight_call_holds():
    """ADK shallow-copies the config when it builds a request.

    So the nested ThinkingConfig is shared with any call already in flight. If
    the setter mutated that object in place, a decision that started at the
    default would finish reported as -- and possibly billed at -- whatever was
    set while it ran, and the prompt log would attribute it to the wrong
    setting.
    """
    captured_by_inflight_request = actor.adk_agent.generate_content_config.thinking_config
    started_at = actor.get_thinking_budget()

    actor.set_thinking_budget(8192)

    assert captured_by_inflight_request.thinking_budget == started_at
    assert actor.get_thinking_budget() == 8192


def test_the_radio_agent_is_not_swept_along_with_the_actor():
    """An experiment on the thing that plays the game stays scoped to it."""
    before = actor.adk_interrogator_agent.generate_content_config.thinking_config.thinking_budget

    actor.set_thinking_budget(2048)

    after = actor.adk_interrogator_agent.generate_content_config.thinking_config.thinking_budget
    assert after == before


# --- the summary the archive reads ---------------------------------------


def test_the_archive_summary_follows_the_live_agent():
    actor.set_thinking_budget(1024)

    summary = actor.summarize_agent(actor.adk_agent)

    assert summary["thinking_budget"] == 1024
    assert summary["thinking_budget_label"] == "1,024 tokens"


def test_auto_is_labelled_rather_than_left_as_minus_one():
    """`-1` reads as a bug to anyone opening an archive months later."""
    actor.set_thinking_budget(-1)

    assert actor.summarize_agent(actor.adk_agent)["thinking_budget_label"] == (
        "auto (model decides)"
    )


# --- the endpoint --------------------------------------------------------


def test_the_endpoint_reports_the_bounds_the_model_actually_has():
    """The UI must not hold its own copy of a model-dependent range."""
    client = TestClient(app)

    body = client.get("/api/agent/thinking-budget").json()

    assert body["model"] == actor.ACTOR_MODEL
    assert body["min"] == actor.THINKING_BUDGET_MIN
    assert body["max"] == actor.THINKING_BUDGET_MAX
    assert body["off"] == 0
    assert body["auto"] == -1


def test_the_endpoint_applies_a_budget():
    client = TestClient(app)

    resp = client.post("/api/agent/thinking-budget", json={"thinking_budget": 512})

    assert resp.status_code == 200
    assert resp.json()["budget"] == 512
    assert actor.get_thinking_budget() == 512


def test_a_rejected_budget_changes_nothing_and_says_why():
    client = TestClient(app)

    resp = client.post("/api/agent/thinking-budget", json={"thinking_budget": 999999})

    assert resp.status_code == 400
    assert "out of range" in resp.json()["detail"]
    # The response still carries what is really in force, so the control can
    # snap back rather than sit on a value the agent refused.
    assert resp.json()["budget"] == actor.THINKING_BUDGET_DEFAULT
    assert actor.get_thinking_budget() == actor.THINKING_BUDGET_DEFAULT


def test_a_bad_timeout_does_not_half_apply_the_request():
    """Both values are validated before either is written."""
    client = TestClient(app)

    resp = client.post(
        "/api/agent/thinking-budget",
        json={"thinking_budget": 2048, "deliberation_timeout_sec": 9999},
    )

    assert resp.status_code == 400
    assert actor.get_thinking_budget() == actor.THINKING_BUDGET_DEFAULT, (
        "the good half was applied anyway"
    )


def test_the_budget_appears_in_the_state_the_workbench_polls():
    client = TestClient(app)
    client.post("/api/agent/thinking-budget", json={"thinking_budget": 1024})

    thinking = client.get("/api/agent/state").json()["thinking"]

    assert thinking["budget"] == 1024
    assert thinking["label"] == "1,024 tokens"


# --- the timeout it is coupled to ----------------------------------------


def test_the_timeout_bounds_how_much_thinking_is_affordable():
    actor.set_deliberation_timeout(8.0)
    narrow = actor.affordable_thinking_tokens()

    actor.set_deliberation_timeout(60.0)

    assert actor.affordable_thinking_tokens() > narrow


def test_a_budget_that_cannot_land_inside_the_timeout_warns():
    """Silence here would produce a run that measured the heuristic.

    An overrun decision is discarded and the tick is played by the reflex, so
    an unaffordable budget does not make a slow cat -- it makes a cat that is
    not using the model, while the box score still says Gemini was configured.
    """
    actor.set_deliberation_timeout(8.0)

    warning = actor.thinking_budget_warning(24576)

    assert warning is not None
    assert "reflex" in warning
    # The warning carries a fix, not just an objection.
    assert "Raise the timeout" in warning


def test_an_affordable_budget_does_not_warn():
    actor.set_deliberation_timeout(60.0)

    assert actor.thinking_budget_warning(512) is None


def test_disabled_thinking_never_warns():
    actor.set_deliberation_timeout(1.0)

    assert actor.thinking_budget_warning(0) is None


def test_the_suggested_timeout_is_enough_for_the_budget():
    actor.set_deliberation_timeout(8.0)

    suggested = actor.suggested_deliberation_timeout(4096)

    assert suggested is not None
    assert actor.affordable_thinking_tokens(suggested) >= 4096


# --- what the archive ends up saying --------------------------------------


def test_a_mid_game_change_is_recorded_against_the_tick_it_happened_on(tmp_path, monkeypatch):
    """A run that moved the dial must not look like one that never did.

    Otherwise an archive invites a comparison between two runs where only one
    of them was controlled.
    """
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))
    import importlib
    importlib.reload(store)
    try:
        client = TestClient(app)
        state.active_tick = 317

        client.post("/api/agent/thinking-budget", json={"thinking_budget": 2048})

        changes = state.thinking_budget_changes
        assert len(changes) == 1
        assert changes[0]["at_tick"] == 317
        assert changes[0]["from"] == actor.THINKING_BUDGET_DEFAULT
        assert changes[0]["to"] == 2048
    finally:
        state.active_tick = 0
        monkeypatch.undo()
        importlib.reload(store)


def test_the_run_config_is_rewritten_when_the_budget_changes(tmp_path, monkeypatch):
    """The archive is copied when the game ends, long before the process does.

    A run_config written once at startup would still be claiming the default an
    hour after the budget was raised.
    """
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))
    import importlib
    import json
    importlib.reload(store)
    # `api` captured the module object at import, so point it at the reloaded one.
    import actors.cat.api as api_module
    monkeypatch.setattr(api_module, "store", store)
    try:
        publish_run_config()
        first = json.loads(store.RUN_CONFIG_PATH.read_text())
        actor_entry = next(a for a in first["agents"] if a["name"] == "cat_tactician")
        assert actor_entry["thinking_budget"] == actor.THINKING_BUDGET_DEFAULT

        client = TestClient(app)
        client.post("/api/agent/thinking-budget", json={"thinking_budget": 4096})

        second = json.loads(store.RUN_CONFIG_PATH.read_text())
        actor_entry = next(a for a in second["agents"] if a["name"] == "cat_tactician")
        assert actor_entry["thinking_budget"] == 4096
        assert second["thinking_budget_changes"][-1]["to"] == 4096
    finally:
        monkeypatch.undo()
        importlib.reload(store)


def test_the_prompt_capture_records_the_budget_that_produced_it():
    """A prompt log straddling a change must stay attributable per decision."""
    from actors.models import Observation

    actor.set_thinking_budget(2048)
    capture = {}
    actor.build_decision_prompt(
        Observation(
            tick=5,
            round=1,
            self={"id": "cat-1", "position": {"x": 1, "y": 1}, "facing": "north"},
            legal_actions=["stay", "north"],
        ),
        capture=capture,
    )

    assert capture["thinking_budget"] == 2048
