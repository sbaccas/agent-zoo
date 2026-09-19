"""Tests for the General Radio -- the General's channel to the Coach.

The channel exists so a human can audit the coaching that reaches the Cat. Two
properties matter more than anything else here and most of these tests defend
one of them:

  1. Asking is not ordering. A question must never change what the coach does.
  2. An order, once given, must actually reach the evaluation prompt and must
     survive the coach rewriting its own dossier.

The second is the one that has a real bug behind it elsewhere in this codebase:
commander orders to the *actor* were silently erased for weeks because they
shared a slot with a value the model overwrote every tick.
"""

import importlib
import json
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import actors.cat.api as api_module
from actors.cat import store
from actors.cat.subagents import eval as ev


@pytest.fixture
def client(monkeypatch):
    """A workbench on a throwaway runtime dir, with the coach directive reset."""
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", tmp)
    importlib.reload(store)
    monkeypatch.setattr(api_module, "store", store)
    monkeypatch.setattr(ev, "store", store)
    ev.clear_coach_directive()
    api_module.state.general_chat_history.clear()
    api_module.state.chat_history.clear()
    try:
        yield TestClient(api_module.app)
    finally:
        ev.clear_coach_directive()
        api_module.state.general_chat_history.clear()
        monkeypatch.undo()
        importlib.reload(store)


# ---------------------------------------------------------------------------
# The persona and the agent
# ---------------------------------------------------------------------------


def test_the_general_radio_persona_file_exists():
    assert ev.GENERAL_RADIO_SKILLS_PATH.exists(), (
        "general_radio_skills.md is loaded by _general_instruction_provider on "
        "every call; without it the agent raises instead of answering."
    )


def test_the_general_speaks_with_the_coachs_manual_not_its_own():
    """The radio agent must share the evaluator's model of the game.

    If it ran on the persona alone it would defend directives using rules it
    invented, which is precisely what eval_skills.md section 1 forbids.
    """
    instruction = ev._general_instruction_provider(None)
    assert ev.get_eval_instruction() in instruction
    assert ev.get_general_radio_instruction() in instruction


def test_the_general_carries_the_boxscore_tool():
    """The persona tells it to fetch numbers rather than recall them.

    Without the tool that instruction is unfollowable, and an agent told to cite
    statistics it cannot fetch will invent them.
    """
    names = [getattr(t, "__name__", str(t)) for t in (ev.general_agent.tools or [])]
    assert "get_boxscore" in names


def test_the_general_is_a_distinct_agent_from_the_evaluator():
    assert ev.general_agent is not ev.eval_agent
    assert ev.general_agent.name != ev.eval_agent.name


def test_the_general_has_its_own_lock():
    """Sharing the evaluator's lock would park a radio call behind an evaluation.

    An evaluation may run for minutes at the top of its timeout range. A human
    waiting that long reads the channel as dead.
    """
    assert ev._general_lock is not ev._eval_lock


def test_the_general_is_held_out_of_the_coachs_thinking_budget_dial():
    """An A/B on coaching quality must not also move the radio."""
    before = ev.general_agent.generate_content_config.thinking_config.thinking_budget
    original = ev.get_eval_thinking_budget()
    try:
        ev.set_eval_thinking_budget(512)
        after = ev.general_agent.generate_content_config.thinking_config.thinking_budget
        assert after == before
    finally:
        ev.set_eval_thinking_budget(original)


# ---------------------------------------------------------------------------
# Property 1: asking is not ordering
# ---------------------------------------------------------------------------


def test_a_plain_question_leaves_the_coach_unconstrained(client):
    client.post("/api/agent/general/prompt", json={"prompt": "why that directive?"})
    assert ev.get_coach_directive() is None
    assert client.get("/api/agent/general/directive").json()["active"] is False


def test_a_plain_question_does_not_touch_the_published_dossier(client):
    store.write_coaching_notes("- Keep your strike zone discipline")
    before = store.read_coaching_notes()
    client.post("/api/agent/general/prompt", json={"prompt": "explain this directive"})
    assert store.read_coaching_notes() == before


def test_steer_requires_an_explicit_opt_in(client):
    """The request model defaults steer_directive to False.

    The actor's channel defaults its equivalent to True, and the asymmetry is
    deliberate: an order to the coach persists across every future evaluation.
    """
    from actors.models import GeneralInterrogateRequest

    assert GeneralInterrogateRequest(prompt="x").steer_directive is False


# ---------------------------------------------------------------------------
# Property 2: an order actually lands, and survives
# ---------------------------------------------------------------------------


def test_steering_records_a_standing_order(client):
    text = "Never cite a metric the cat cannot observe."
    r = client.post(
        "/api/agent/general/prompt",
        json={"prompt": text, "steer_directive": True},
    ).json()
    assert r["adopted_directive"] == text
    directive = ev.get_coach_directive()
    assert directive["directive"] == text
    assert directive["source"] == "general_radio"


def test_the_standing_order_reaches_the_evaluation_prompt(client):
    text = "Stop optimising unique_tiles."
    client.post(
        "/api/agent/general/prompt",
        json={"prompt": text, "steer_directive": True},
    )
    prompt = ev.build_eval_prompt(
        [], "- some existing directive", (ev.get_coach_directive() or {}).get("directive")
    )
    assert ev.COACH_DIRECTIVE_BLOCK_HEADER in prompt
    assert text in prompt


def test_the_order_is_placed_after_the_dossier_so_it_wins(client):
    """Ordering is load-bearing, not cosmetic.

    The dossier block instructs the coach that carrying directives forward
    verbatim is the default. An order buried before that instruction loses to
    it, and the General would watch their order get overwritten every cycle
    with no error raised anywhere.
    """
    prompt = ev.build_eval_prompt([], "- old directive", "contradict the old directive")
    assert prompt.index(ev.COACH_DIRECTIVE_BLOCK_HEADER) > prompt.index(
        "YOUR STANDING DIRECTIVES SO FAR"
    )


def test_no_order_means_no_block_at_all(client):
    """An absent order must not leave an empty header behind.

    A bare header reads to the model as an order it cannot see, which is worse
    than silence.
    """
    prompt = ev.build_eval_prompt([], "- old directive")
    assert ev.COACH_DIRECTIVE_BLOCK_HEADER not in prompt


def test_a_whitespace_only_order_is_not_an_order(client):
    prompt = ev.build_eval_prompt([], "- old directive", "   \n  ")
    assert ev.COACH_DIRECTIVE_BLOCK_HEADER not in prompt


def test_the_order_is_recorded_even_when_the_coach_is_offline(client):
    """The order is the General's, not the Coach's.

    Under pytest the radio always takes the offline path. Losing the order
    because the officer happened to be off the air would be a real data loss.
    """
    r = client.post(
        "/api/agent/general/prompt",
        json={"prompt": "hold this order", "steer_directive": True},
    ).json()
    assert r["engine"] == "offline_fallback"
    assert ev.get_coach_directive()["directive"] == "hold this order"


def test_rescinding_removes_the_order_from_the_prompt(client):
    client.post(
        "/api/agent/general/prompt",
        json={"prompt": "an order", "steer_directive": True},
    )
    assert client.post("/api/agent/general/clear-directive").json()["cleared"] is True
    assert ev.get_coach_directive() is None
    prompt = ev.build_eval_prompt([], "- d", (ev.get_coach_directive() or {}).get("directive"))
    assert ev.COACH_DIRECTIVE_BLOCK_HEADER not in prompt


def test_rescinding_nothing_reports_that_nothing_was_rescinded(client):
    assert client.post("/api/agent/general/clear-directive").json()["cleared"] is False


# ---------------------------------------------------------------------------
# Channel isolation
# ---------------------------------------------------------------------------


def test_the_two_radios_keep_separate_logs(client):
    client.post("/api/agent/general/prompt", json={"prompt": "to the general"})
    assert len(client.get("/api/agent/general/chat-history").json()) == 2
    assert client.get("/api/agent/chat-history").json() == []


def test_the_general_log_attributes_replies_to_the_general(client):
    client.post("/api/agent/general/prompt", json={"prompt": "hello"})
    log = client.get("/api/agent/general/chat-history").json()
    # The human operator is the General on this channel; the Coach answers.
    # One rung down, on Box 4, the same human speaks as the Commander and the
    # Cat answers. Pinning the pair here is what caught the roles being
    # installed backwards the first time.
    assert [m["sender"] for m in log] == ["general", "coach"]


def test_clearing_the_log_does_not_rescind_the_order(client):
    """Tidying a conversation and revoking an order are different intentions.

    Coupling them would mean the General lost a constraint they never asked
    to drop, and the order text is not recoverable once the log is gone.
    """
    client.post(
        "/api/agent/general/prompt",
        json={"prompt": "an order", "steer_directive": True},
    )
    client.post("/api/agent/general/clear-chat")
    assert client.get("/api/agent/general/chat-history").json() == []
    assert client.get("/api/agent/general/directive").json()["active"] is True


def test_clearing_the_general_log_leaves_the_actor_log_alone(client):
    api_module.state.chat_history.append({"sender": "cat", "message": "hi"})
    client.post("/api/agent/general/clear-chat")
    assert len(client.get("/api/agent/chat-history").json()) == 1


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


def test_an_empty_transmission_is_rejected_without_calling_the_model(client):
    r = client.post("/api/agent/general/prompt", json={"prompt": "   "}).json()
    assert "empty transmission" in r["reply"]
    assert client.get("/api/agent/general/chat-history").json() == []


@pytest.mark.asyncio
async def test_the_offline_reply_refuses_to_invent_a_rationale():
    """The fallback must not fabricate a defence of the coaching.

    The General is on this channel to audit the coaching. A plausible-sounding
    but invented justification is the one reply that actively misleads them --
    strictly worse than admitting the officer is off the air.
    """
    reply, engine, err = await ev.interrogate_general(
        "why are you telling the cat to maximise unique_tiles?",
        notes="- Expand Exploration: increase your unique_tiles",
    )
    assert engine == "offline_fallback"
    assert "off the air" in reply
    assert "unique_tiles" not in reply


@pytest.mark.asyncio
async def test_the_offline_reply_states_the_coaching_is_unchanged():
    """A failed radio call must not leave the General guessing."""
    reply, _, _ = await ev.interrogate_general("status inquiry", notes="- a\n- b")
    assert "unchanged" in reply
    assert "2 directive(s)" in reply


def test_the_reply_reports_how_many_directives_are_in_force(client):
    store.write_coaching_notes("- one\n- two\n- three")
    r = client.post("/api/agent/general/prompt", json={"prompt": "status"}).json()
    assert r["notes_in_force"] == 3


# ---------------------------------------------------------------------------
# Endpoint surface
# ---------------------------------------------------------------------------


def test_the_directive_endpoint_reports_the_channel_configuration(client):
    payload = client.get("/api/agent/general/directive").json()
    assert payload["timeout_sec"] == ev.GENERAL_RADIO_TIMEOUT_SECONDS
    assert payload["skills_path"].endswith("general_radio_skills.md")


def test_the_radio_timeout_is_far_below_the_evaluation_timeout():
    """A human is waiting on this call; an evaluation is a background job."""
    assert ev.GENERAL_RADIO_TIMEOUT_SECONDS < ev.EVAL_TIMEOUT_MIN_SEC


# ---------------------------------------------------------------------------
# The UI contract
# ---------------------------------------------------------------------------


def _static(name: str) -> str:
    return (Path(__file__).parent.parent / "actors" / "cat" / "static" / name).read_text(
        encoding="utf-8"
    )


def test_every_general_radio_element_the_script_reads_exists_in_the_markup():
    """getElementById returning null fails silently and the box just does nothing."""
    html = _static("index.html")
    for element_id in (
        "generalChatMessages",
        "generalPromptForm",
        "inputGeneralPrompt",
        "btnSendGeneralPrompt",
        "sendGeneralIcon",
        "sendGeneralText",
        "checkSteerCoach",
        "generalTypingIndicator",
        "generalStandingOrder",
        "generalStandingOrderText",
        "btnClearGeneralChat",
        "btnClearGeneralDirective",
    ):
        assert f'id="{element_id}"' in html, f"{element_id} missing from index.html"


def test_the_steer_checkbox_is_unchecked_in_the_markup():
    """The default must match the API default, or the UI lies about the risk."""
    html = _static("index.html")
    idx = html.index('id="checkSteerCoach"')
    tag = html[html.rindex("<", 0, idx) : html.index(">", idx) + 1]
    assert "checked" not in tag


def test_the_general_presets_are_tagged_for_their_own_handler():
    """Sharing .btn-preset-chip alone would wire coach presets to the cat's form."""
    html = _static("index.html")
    assert html.count("general-preset") >= 4


def test_the_script_targets_the_general_endpoints():
    js = _static("workbench.js")
    for route in (
        "/api/agent/general/prompt",
        "/api/agent/general/chat-history",
        "/api/agent/general/clear-chat",
        "/api/agent/general/clear-directive",
        "/api/agent/general/directive",
    ):
        assert route in js, f"{route} is never called from workbench.js"
