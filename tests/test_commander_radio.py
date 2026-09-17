"""The commander radio: making a human order actually reach a decision.

The reported symptom was "it pretty much does not listen to my commander
instructions when I send them". It wasn't ignoring them. It never received
them. Four independent breaks stood between the radio and the tick loop:

1. `query_adk_gemini` had no directive parameter, so the decision prompt could
   not carry one even in principle.
2. `act_async` read only `target_mouse_id` and `strategy` off `active_directive`,
   never the order text.
3. The interrogate endpoint hardcoded `"strategy": "ROAMING"` regardless of input.
4. **`_run_background_deliberation` overwrote `state.active_directive` wholesale**
   with Gemini's own decision -- the same slot the commander wrote into. An order
   survived a tick or two at most.

Break 4 is the one that made the others hard to see, and it is the reason the
commander now owns a separate `state.commander_directive` slot. The test that
pins it down is `test_model_deliberation_cannot_erase_a_commander_order`.

Precedence is asserted here as well, because a chain of command that is only
described in prose and never checked is a chain of command that drifts.
"""

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from actors.cat import store
from actors.cat.actor import build_decision_prompt
from actors.cat.api import (
    _run_background_deliberation,
    app,
    query_adk_gemini as api_query_adk_gemini,
    reset_reflexion_state,
    state,
)
from actors.models import CatTacticalDecision, Observation

SKILLS_MD = Path(__file__).resolve().parents[1] / "actors/cat/actor_skills.md"

ORDER = "head west and sweep the ridge line"


@pytest.fixture(autouse=True)
def clean_state():
    state.commander_directive = None
    state.active_directive = None
    yield
    state.commander_directive = None
    state.active_directive = None


def _observation(tick=30):
    return Observation(
        tick=tick,
        round=1,
        self={"id": "cat-1", "position": {"x": 19, "y": 51}, "facing": "east"},
        visible_entities=[],
        legal_actions=["stay", "north", "south", "east", "west", "attack"],
        capabilities={"attack_range": 2, "vision_radius": 4},
        score=0,
    )


def _directive(text=ORDER, tick=24):
    return {"directive": text, "tick": tick, "source": "commander_radio"}


# --------------------------------------------------------------------------
# Delivery into the prompt
# --------------------------------------------------------------------------

def test_order_text_reaches_the_decision_prompt_verbatim():
    """The whole point. Before this, the text existed only in the chat log."""
    prompt = build_decision_prompt(
        _observation(), history=[], commander_directive=_directive()
    )
    assert ORDER in prompt, (
        "the commander's words are absent from the prompt the model answers; "
        "this is the original bug, where orders reached only the chat reply"
    )


def test_order_is_attributed_to_the_commander_not_left_as_bare_text():
    """Unlabelled text in a prompt is indistinguishable from the model's own notes."""
    prompt = build_decision_prompt(
        _observation(), history=[], commander_directive=_directive()
    )
    assert "COMMANDER" in prompt
    assert "RADIO" in prompt.upper()


def test_order_block_is_last_so_it_sits_closest_to_the_answer():
    """Position is the mechanism, not decoration.

    The complaint was that orders were ignored. Burying the order above ~12KB of
    skills manual, coaching notes and history invites exactly that, so the block
    is placed after the observation, immediately before the model commits.
    """
    prompt = build_decision_prompt(
        _observation(), history=[], commander_directive=_directive()
    )
    order_at = prompt.index(ORDER)
    observation_at = prompt.index("CURRENT OBSERVATION")
    assert order_at > observation_at, (
        "the commander block was moved above the observation; it must be the "
        "last thing the model reads before answering"
    )


def test_absent_order_adds_nothing_to_the_prompt():
    """Silence must cost zero tokens and introduce no empty headings."""
    quiet = build_decision_prompt(_observation(), history=[], commander_directive=None)
    assert "COMMANDER" not in quiet
    assert "RADIO" not in quiet.upper()


@pytest.mark.parametrize("empty", [None, {}, {"directive": ""}, {"directive": "   "}])
def test_blank_orders_are_treated_as_no_order(empty):
    """A whitespace-only order must not produce an empty, confusing block."""
    prompt = build_decision_prompt(_observation(), history=[], commander_directive=empty)
    assert "LIVE ORDER FROM YOUR COMMANDER" not in prompt


def test_order_states_its_age_in_raw_numbers():
    """Report the tick and the delta; let the model judge staleness.

    Consistent with how score is handled -- raw counters, no interpretation.
    Supplying a "this order is stale" verdict would be exactly the pre-chewed
    conclusion this codebase avoids.
    """
    prompt = build_decision_prompt(
        _observation(tick=30), history=[], commander_directive=_directive(tick=24)
    )
    assert "tick 24" in prompt
    assert "6 tick(s) ago" in prompt


def test_age_never_reads_negative_after_a_tick_rewind():
    """A reset rewinds the clock; an order from tick 24 must not read as -24."""
    prompt = build_decision_prompt(
        _observation(tick=2), history=[], commander_directive=_directive(tick=24)
    )
    assert "-" not in re.search(r"(\d+|-\d+) tick\(s\) ago", prompt).group(0)


def test_prompt_states_no_compliance_rule_of_its_own():
    """All imperatives live in the manual; the prompt carries data only.

    This previously asserted the opposite -- that the prompt itself spelled out
    the order's authority. That framing ("OUTRANKS ... Comply this tick") turned
    a single question from the commander into 100 consecutive lunges, because it
    re-fired on every tick for as long as the message was in the window. The
    rule now lives in actor_skills.md, stated once.
    """
    prompt = build_decision_prompt(
        _observation(), history=[], commander_directive=_directive()
    )
    lowered = prompt.lower()
    for banned in ("outranks", "comply this tick", "highest authority", "you must"):
        assert banned not in lowered, (
            f"the prompt is instructing the model again ({banned!r}); "
            f"this belongs in actor_skills.md"
        )
    # The data itself must still be there.
    assert ORDER in prompt
    assert "tick(s) ago" in prompt


def test_manual_carries_the_compliance_rule_the_prompt_dropped():
    """Removing framing from the prompt is only safe if the manual has it."""
    manual = SKILLS_MD.read_text().lower()
    assert "legal_actions" in manual, (
        "nothing tells the model an order cannot force an illegal action"
    )
    assert "precedence" in manual, "the manual never ranks the channels"


def test_manual_tells_the_model_a_question_is_not_an_order():
    """The live failure: "how come you never lunge?" became a standing order.

    It was interrogative, but the prompt framing said "comply this tick", so the
    model lunged on 100 of the next 110 ticks.

    This asserts the rule sentence itself, inside the commander section. An
    earlier version merely checked that the words "question" and "standing
    order" appeared somewhere in the manual, which a 200-line document about a
    radio channel satisfies almost by accident -- mutation testing showed the
    rule could be deleted with the test still green.
    """
    manual = SKILLS_MD.read_text()
    start = manual.index("### The Commander")
    section = manual[start:manual.index("### The Coach")].lower()

    assert "distinguish an order from a question" in section, (
        "the commander section never draws the order/question distinction"
    )
    assert "do not convert a question into a standing order" in section, (
        "the manual identifies questions but never forbids promoting one to a "
        "standing order, which is the exact failure observed"
    )


# --------------------------------------------------------------------------
# Capture / inspector agreement
# --------------------------------------------------------------------------

def test_inspector_reports_the_order_as_delivered():
    capture = {}
    build_decision_prompt(
        _observation(), history=[], commander_directive=_directive(), capture=capture
    )
    section = capture["sections"]["commander_directive"]
    assert section["delivered"] is True
    assert ORDER in section["text"]


def test_inspector_section_matches_what_the_prompt_actually_contains():
    """The inspector must not become a plausible-looking fiction."""
    capture = {}
    prompt = build_decision_prompt(
        _observation(), history=[], commander_directive=_directive(), capture=capture
    )
    assert capture["sections"]["commander_directive"]["text"] in prompt


# --------------------------------------------------------------------------
# State ownership: the bug that made orders evaporate
# --------------------------------------------------------------------------

def test_model_deliberation_cannot_erase_a_commander_order():
    """Break #4, pinned by driving the real background deliberation.

    The commander and the model used to share `state.active_directive`.
    `_run_background_deliberation` assigns that slot wholesale on every
    completed deliberation, so an order was erased within a tick or two. This
    runs that exact function and requires the order to survive it.
    """
    state.commander_directive = _directive()
    decision = CatTacticalDecision(
        action="east", thought="exploring east", strategy="ROAMING", target_mouse_id=""
    )

    async def _drive():
        with patch("actors.cat.api.model_query_adk_gemini",
                   new=AsyncMock(return_value=(decision, "adk_gemini", None))) as mock:
            await _run_background_deliberation(_observation(tick=31))
            return mock

    mock = asyncio.run(_drive())

    assert state.commander_directive is not None, (
        "the model's own deliberation wiped the commander's standing order; "
        "this is the original bug and it means the two are sharing a slot again"
    )
    assert state.commander_directive["directive"] == ORDER

    # It must also have been handed to the model on that very call.
    assert mock.await_args.kwargs.get("commander_directive") == _directive(), (
        "the background path -- which decides most Gemini ticks -- did not "
        "forward the standing order to the model"
    )


def test_synchronous_path_also_forwards_the_order():
    """Both model paths must carry it, not just the decoupled one.

    The synchronous wrapper runs whenever decoupled mode is off. Wiring only the
    background path would leave a mode in which orders silently vanish again.
    """
    state.commander_directive = _directive()
    decision = CatTacticalDecision(
        action="west", thought="complying", strategy="ROAMING", target_mouse_id=""
    )

    async def _drive():
        with patch("actors.cat.api.model_query_adk_gemini",
                   new=AsyncMock(return_value=(decision, "adk_gemini", None))) as mock:
            await api_query_adk_gemini(_observation(tick=31))
            return mock

    mock = asyncio.run(_drive())
    assert mock.await_args.kwargs.get("commander_directive") == _directive(), (
        "the synchronous path did not forward the standing order"
    )


def test_radio_survives_an_observation_with_null_capabilities():
    """A real 500, found by these tests running after one another.

    `Observation.capabilities` is Optional and defaults to None. The endpoint
    guarded only against a *missing* observation, so a present one carrying
    nulls reached `caps.get(...)` and crashed. Sending a radio message is the
    one action a human takes by hand, so it must not depend on the shape of
    whatever observation happens to be cached.
    """
    state.latest_observation = {
        "tick": 5,
        "round": 1,
        "self": {"id": "cat-1", "position": {"x": 1, "y": 1}, "facing": "north"},
    }
    client = TestClient(app)
    resp = client.post("/api/agent/prompt", json={"prompt": "status?"})
    assert resp.status_code == 200, (
        f"the radio 500s when the cached observation has null capabilities: "
        f"{resp.text[:300]}"
    )


def test_endpoint_adopts_an_order_only_when_steering_is_requested():
    client = TestClient(app)

    client.post("/api/agent/prompt", json={"prompt": "what do you see?"})
    assert state.commander_directive is None, (
        "a plain question was adopted as a standing order"
    )

    client.post("/api/agent/prompt", json={"prompt": ORDER, "steer_directive": True})
    assert state.commander_directive is not None
    assert state.commander_directive["directive"] == ORDER


def test_endpoint_writes_to_the_commanders_own_slot():
    """Regression guard: writing to `active_directive` reintroduces break #4."""
    client = TestClient(app)
    client.post("/api/agent/prompt", json={"prompt": ORDER, "steer_directive": True})

    assert state.commander_directive is not None
    assert (state.active_directive or {}).get("directive") != ORDER, (
        "the order was written into the model's slot, where the next background "
        "deliberation will overwrite it"
    )


def test_a_new_order_supersedes_the_previous_one():
    client = TestClient(app)
    client.post("/api/agent/prompt", json={"prompt": ORDER, "steer_directive": True})
    client.post("/api/agent/prompt", json={"prompt": "hold position", "steer_directive": True})
    assert state.commander_directive["directive"] == "hold position"


def test_order_can_be_read_back_with_its_age():
    client = TestClient(app)
    state.active_tick = 40
    state.commander_directive = _directive(tick=24)

    data = client.get("/api/agent/directive").json()
    assert data["directive"]["directive"] == ORDER
    assert data["age_ticks"] == 16


def test_order_can_be_cancelled():
    """Orders stand until cancelled, so cancelling has to be possible."""
    client = TestClient(app)
    state.commander_directive = _directive()

    first = client.request("DELETE", "/api/agent/directive").json()
    assert first["cleared"] is True
    assert state.commander_directive is None

    second = client.request("DELETE", "/api/agent/directive").json()
    assert second["cleared"] is False, "cancelling twice should report nothing to clear"


def test_reset_clears_the_standing_order():
    """The order names a world that no longer exists, and its age would lie.

    After a reset the tick counter returns to 0, so an order stamped tick 24
    would report a nonsensical age forever.
    """
    state.commander_directive = _directive()
    reset_reflexion_state(current_tick=0)
    assert state.commander_directive is None


def test_no_order_reads_back_cleanly_rather_than_erroring():
    client = TestClient(app)
    data = client.get("/api/agent/directive").json()
    assert data["status"] == "ok"
    assert data["directive"] is None
    assert data["age_ticks"] is None


# --------------------------------------------------------------------------
# The skills manual
# --------------------------------------------------------------------------

def test_manual_quotes_the_exact_headings_the_prompt_emits():
    """The manual must describe the prompt the model actually receives.

    Grepping for the word "commander" is close to unfalsifiable. What matters is
    that the manual names the literal block headings `build_decision_prompt`
    emits, so the model can match what it is reading to what it was taught.

    The headings are imported from the module rather than retyped here, so this
    fails if a header is renamed in code without being renamed in the manual --
    which now matters more than it used to, because the manual is the only place
    the weighting rules exist.
    """
    from actors.cat.actor import COACH_BLOCK_HEADER, COMMANDER_BLOCK_HEADER

    manual = SKILLS_MD.read_text()

    commander_prompt = build_decision_prompt(
        _observation(), history=[], commander_directive=_directive()
    )
    assert COMMANDER_BLOCK_HEADER in commander_prompt, (
        "the prompt no longer emits the commander heading it declares"
    )
    assert COMMANDER_BLOCK_HEADER in manual, (
        f"the manual never names the heading the model actually sees "
        f"({COMMANDER_BLOCK_HEADER!r}), so it cannot connect the two"
    )
    assert COACH_BLOCK_HEADER in manual, (
        f"the manual never names the coaching block heading "
        f"({COACH_BLOCK_HEADER!r}); coaching notes are injected into every "
        f"prompt under a heading the model has never been told the meaning of"
    )


def test_coach_block_uses_the_declared_heading():
    """The coach heading is only exercised when notes exist on disk."""
    from actors.cat import actor as actor_mod

    original = actor_mod.store.read_coaching_notes
    actor_mod.store.read_coaching_notes = lambda: "- keep to the south corridor"
    try:
        prompt = build_decision_prompt(_observation(), history=[])
    finally:
        actor_mod.store.read_coaching_notes = original

    assert actor_mod.COACH_BLOCK_HEADER in prompt
    assert "- keep to the south corridor" in prompt


# --------------------------------------------------------------------------
# Block order and the decay of an ageing order
# --------------------------------------------------------------------------


def _prompt_with_notes(observation, history=None, directive=None):
    """Build a prompt with coaching notes present, restoring the reader after."""
    from actors.cat import actor as actor_mod

    original = actor_mod.store.read_coaching_notes
    actor_mod.store.read_coaching_notes = lambda: "- COACHMARK keep to the south corridor"
    try:
        return build_decision_prompt(
            observation, history=history or [], commander_directive=directive
        )
    finally:
        actor_mod.store.read_coaching_notes = original


def _history(n=3):
    return [
        {
            "tick": t,
            "observation": {"self": {"position": {"x": 1, "y": 1}}, "score": 0},
            "response": {"action": "east", "thought": "HISTORYMARK"},
        }
        for t in range(1, n + 1)
    ]


def test_blocks_are_ordered_by_ascending_influence():
    """history -> coach -> observation -> commander.

    The model answers immediately after the final token, so the last block read
    is the one nearest the decision. The coach used to lead, which left ~10,400
    characters between its directives and the answer while the history window
    -- five times its size, and made of examples of the behaviour being
    corrected -- sat closer. Order is the cheapest lever available and this
    pins it.
    """
    prompt = _prompt_with_notes(
        _observation(tick=30), history=_history(), directive=_directive(tick=28)
    )

    history_at = prompt.index("RECENT TACTICAL CONTEXT")
    coach_at = prompt.index("COACHMARK")
    observation_at = prompt.index("CURRENT OBSERVATION")
    order_at = prompt.index(ORDER)

    assert history_at < coach_at < observation_at < order_at, (
        "blocks are not in ascending order of influence; the coach must sit "
        "after the history window and the live order must sit last"
    )


def test_ageing_order_slides_away_from_the_decision():
    """An order decays by position rather than by suppression.

    Dropping it would destroy something the commander deliberately sent, and
    stamping it "stale" would be the pre-chewed verdict this prompt refuses to
    supply. Moving it from the most influential slot to the least lets it fade
    without either. Measured motivation: across 198 live prompts carrying an
    order the median age was 42 ticks, so the most valuable position in the
    prompt was routinely occupied by something long overtaken by events.
    """
    from actors.cat.actor import COMMANDER_FRESH_TICKS

    fresh = _prompt_with_notes(
        _observation(tick=30),
        history=_history(),
        directive=_directive(tick=30 - COMMANDER_FRESH_TICKS),
    )
    assert fresh.index(ORDER) > fresh.index("CURRENT OBSERVATION"), (
        "an order still inside the freshness window must remain last"
    )

    stale = _prompt_with_notes(
        _observation(tick=30),
        history=_history(),
        directive=_directive(tick=30 - COMMANDER_FRESH_TICKS - 1),
    )
    assert stale.index(ORDER) < stale.index("RECENT TACTICAL CONTEXT"), (
        "an order past the freshness window must lose the slot nearest the "
        "decision"
    )


def test_a_decayed_order_is_still_delivered_in_full():
    """Demotion must not become censorship, nor acquire a verdict.

    The text, the issuing tick and the age all survive the move; only position
    changes. If this ever starts dropping orders, a commander typing into the
    radio gets silence with no indication why.
    """
    stale = _prompt_with_notes(
        _observation(tick=300), history=_history(), directive=_directive(tick=24)
    )

    assert ORDER in stale, "the order text was discarded rather than demoted"
    assert "tick 24" in stale
    assert "276 tick(s) ago" in stale
    for verdict in ("stale", "outdated", "expired", "ignore", "no longer"):
        assert verdict not in stale.lower(), (
            f"the prompt now tells the model the order is {verdict!r}; judging "
            f"staleness from the stated age is the model's job"
        )


def test_manual_has_a_dedicated_chain_of_command_section():
    """Mentions scattered through the text are not an explanation."""
    headings = re.findall(r"^## \d+\. (.+)$", SKILLS_MD.read_text(), re.M)
    joined = " | ".join(headings).lower()
    assert "command" in joined, (
        f"no section explains the chain of command; headings are: {headings}"
    )



def test_manual_states_the_precedence_order():
    """Parse the numbered rungs; do not just scan the block for the words.

    A previous version searched the whole precedence section for "commander"
    and "coach" and compared offsets. Rung 1 reads "this overrides everyone,
    including the commander", so "commander" always appeared first no matter
    how rungs 2 and 3 were ordered -- the test passed even with the coach
    ranked above the commander. Mutation testing caught it.
    """
    text = SKILLS_MD.read_text()
    assert "Precedence" in text, "the manual describes the channels but not their ranking"
    idx = text.index("Precedence")
    block = text[idx:idx + 700]

    rungs = dict(re.findall(r"^(\d+)\. (.+)$", block, re.M))
    assert {"1", "2", "3", "4"} <= set(rungs), f"precedence list is malformed: {rungs}"

    assert "legal" in rungs["1"].lower(), (
        f"rung 1 must be legality, got: {rungs['1']!r}"
    )
    assert "commander" in rungs["2"].lower(), (
        f"the commander must be rung 2, got: {rungs['2']!r}"
    )
    assert "coach" in rungs["3"].lower(), (
        f"the coach must be rung 3, got: {rungs['3']!r}"
    )


def test_manual_keeps_legality_above_every_advisor():
    """No instruction from anyone may produce an action outside legal_actions."""
    text = SKILLS_MD.read_text()
    idx = text.index("Precedence")
    block = text[idx:idx + 600].lower()
    assert "legal" in block, (
        "the precedence list does not place legality above the commander; an "
        "order could then coax an illegal action out of the model"
    )


def test_manual_section_numbering_has_no_duplicates():
    """The new section was inserted mid-document; renumbering is easy to botch."""
    headings = re.findall(r"^## (\d+)\.", SKILLS_MD.read_text(), re.M)
    assert headings == sorted(headings, key=int), f"sections out of order: {headings}"
    assert len(headings) == len(set(headings)), f"duplicate section numbers: {headings}"
