"""The Gemini payload inspector: what the model actually saw, per decision.

Box 5 of the workbench labels rows `ADK Gemini`, but until now there was no way
to see the prompt behind such a row. This suite covers the whole path: the
on-disk log, the lookup key, the HTTP endpoint, the section breakdown, and the
JS renderer.

Two properties matter more than the rest and are pinned hard.

1. **Records are keyed by `applied_at_tick`, not `built_at_tick`.** In the
   decoupled System-1/System-2 path a deliberation begins during one tick and is
   consumed a tick or more later. The user clicks a *trajectory row*, so the
   lookup must use the tick the decision was applied to. Keying on the built
   tick returns the wrong prompt -- or nothing -- for exactly the rows anyone
   would want to inspect.

2. **`commander_directive` reports `delivered: False`.** This is not a
   placeholder awaiting implementation; it is the finding. `query_adk_gemini`
   accepts no directive argument, so radio messages never reach the decision
   prompt. If that section ever silently flips to delivered while the plumbing
   is still absent, the inspector would be lying about the one thing the user
   asked it to reveal.

The JS half runs the real `renderPromptRecord` in JavaScriptCore against a fake
DOM rather than grepping the source, because `node` is not installed here.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from actors.cat import store
from actors.cat.actor import build_decision_prompt
from actors.cat.api import app, reset_reflexion_state, state
from actors.models import Observation

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_JS = ROOT / "actors/cat/static/workbench.js"
INDEX_HTML = ROOT / "actors/cat/static/index.html"
WORKBENCH_CSS = ROOT / "actors/cat/static/workbench.css"

JSC = Path(
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
)

SECTION_KEYS = [
    "system_instruction",
    "coaching_notes",
    "commander_directive",
    "history_window",
    "current_observation",
]


@pytest.fixture(autouse=True)
def clean_prompt_log():
    """Each test owns the log outright; conftest already redirects it to /tmp."""
    store.reset_prompts()
    yield
    store.reset_prompts()


def _record(applied, built=None, **extra):
    rec = {
        "applied_at_tick": applied,
        "built_at_tick": applied if built is None else built,
        "model": "gemini-2.5-flash",
        "latency_ms": 1313,
        "sections": {k: {"label": k, "delivered": True, "text": k} for k in SECTION_KEYS},
        "full_prompt": f"prompt-for-{applied}",
    }
    rec.update(extra)
    return rec


def _observation(tick=7, score=3):
    return Observation(
        tick=tick,
        round=1,
        self={"id": "cat-1", "position": {"x": 19, "y": 51}, "facing": "east"},
        visible_entities=[{"id": "mouse-42", "position": {"x": 21, "y": 51}}],
        legal_actions=["stay", "north", "south", "east", "west", "attack"],
        capabilities={"attack_range": 2, "vision_radius": 4},
        score=score,
        scoreboard={"cat-1": score, "cat-2": 9},
    )


# --------------------------------------------------------------------------
# Store: the on-disk log
# --------------------------------------------------------------------------

def test_append_and_read_round_trip():
    store.append_prompt(_record(5))
    got = store.read_prompt_for_tick(5)
    assert got is not None, "a record written a moment ago could not be read back"
    assert got["full_prompt"] == "prompt-for-5"


def test_lookup_is_keyed_on_the_applied_tick_not_the_built_tick():
    """The off-by-one the log itself exposed: built at 2, applied at 3.

    A live capture recorded `built_at_tick: 2 -> applied_at_tick: 3`. The
    trajectory row the user clicks is tick 3, so that is the only key that can
    find this record.
    """
    store.append_prompt(_record(applied=3, built=2))

    assert store.read_prompt_for_tick(3) is not None, (
        "lookup missed the applied tick; clicking the Gemini row in the "
        "trajectory would report no payload for a decision that has one"
    )
    assert store.read_prompt_for_tick(2) is None, (
        "lookup matched the built tick; tick 2 was decided by something else "
        "and must not be credited with this prompt"
    )


def test_duplicate_applied_tick_returns_the_newest():
    """Scanning backwards keeps the answer consistent with the row on screen."""
    store.append_prompt(_record(9, full_prompt="stale"))
    store.append_prompt(_record(9, full_prompt="fresh"))
    assert store.read_prompt_for_tick(9)["full_prompt"] == "fresh"


def test_missing_tick_reads_as_none_not_an_error():
    store.append_prompt(_record(1))
    assert store.read_prompt_for_tick(999) is None


def test_reset_wipes_the_log():
    store.append_prompt(_record(4))
    assert store.prompt_log_size()["records"] == 1
    store.reset_prompts()
    assert store.prompt_log_size() == {"bytes": 0, "records": 0}
    assert store.read_prompt_for_tick(4) is None


def test_prompt_log_size_counts_records_and_bytes():
    store.append_prompt(_record(1))
    store.append_prompt(_record(2))
    size = store.prompt_log_size()
    assert size["records"] == 2
    assert size["bytes"] > 0


def test_corrupt_line_does_not_break_the_reader():
    """Diagnostics must never take the workbench down with them."""
    store.append_prompt(_record(6))
    with store.PROMPTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
    assert store.read_prompt_for_tick(6) is not None


# --------------------------------------------------------------------------
# Reset wiring
# --------------------------------------------------------------------------

def test_game_reset_clears_the_prompt_log_and_the_parked_bundle():
    """Prompts are scoped to one game, and grow ~22KB per Gemini tick.

    A prompt left behind after a reset would also be served for a tick number
    the new game will reuse, attributing an old game's reasoning to a fresh
    decision.
    """
    store.append_prompt(_record(11))
    state.latest_deliberated_prompt = {"built_at_tick": 11}

    reset_reflexion_state(current_tick=0)

    assert store.prompt_log_size()["records"] == 0, (
        "the prompt log survived a game reset; the next game's tick numbers "
        "would collide with the previous game's records"
    )
    assert state.latest_deliberated_prompt is None, (
        "a deliberation parked before the reset would be filed against the "
        "new game's first tick"
    )


# --------------------------------------------------------------------------
# HTTP endpoint
# --------------------------------------------------------------------------

def test_endpoint_returns_the_record_for_a_captured_tick():
    store.append_prompt(_record(applied=3, built=2))
    client = TestClient(app)
    data = client.get("/api/agent/prompt/3").json()

    assert data["status"] == "ok"
    assert data["record"]["built_at_tick"] == 2
    assert data["record"]["applied_at_tick"] == 3


def test_endpoint_explains_an_empty_log_differently_from_a_reflex_tick():
    """A miss is expected, so the reason has to be specific to be useful.

    Most ticks are decided by the System-1 reflex and genuinely have no prompt.
    That is a different situation from "nothing has been captured at all", and
    collapsing the two would leave the user guessing which one they hit.

    Both lookups use the SAME tick on purpose. The tick number is interpolated
    into one of the messages, so comparing different ticks would show a
    difference even if the branch distinguishing the two cases were gone.
    """
    client = TestClient(app)

    empty = client.get("/api/agent/prompt/3").json()
    assert empty["status"] == "not_found"
    assert empty["record"] is None

    # Log is now non-empty, but still holds nothing for tick 3.
    store.append_prompt(_record(9))
    reflex = client.get("/api/agent/prompt/3").json()
    assert reflex["status"] == "not_found"

    assert empty["detail"] != reflex["detail"], (
        "an empty log and a reflex tick produce the same message; the user "
        "cannot tell 'nothing was captured' from 'this tick never called Gemini'"
    )
    assert "reflex" in reflex["detail"].lower(), (
        "the miss reason does not name the reflex, which is the actual cause "
        "for the large majority of ticks"
    )


def test_prompt_log_stats_endpoint_reports_growth():
    store.append_prompt(_record(1))
    client = TestClient(app)
    data = client.get("/api/agent/prompt-log/stats").json()
    assert data["status"] == "ok"
    assert data["records"] == 1
    assert data["bytes"] > 0
    assert data["path"].endswith("prompts.jsonl")


# --------------------------------------------------------------------------
# Capture: the section breakdown
# --------------------------------------------------------------------------

def test_capture_records_every_section_separately():
    """The user asked to see the sources split out, not one concatenated blob."""
    capture = {}
    build_decision_prompt(_observation(), history=[], capture=capture)

    assert set(capture["sections"]) == set(SECTION_KEYS), (
        f"section set changed: {sorted(capture['sections'])}"
    )
    for key, sec in capture["sections"].items():
        assert sec["label"], f"{key} has no human-readable label"
        assert isinstance(sec["delivered"], bool), f"{key} has no delivered flag"
        assert isinstance(sec["text"], str) and sec["text"], f"{key} has no text"


def test_commander_section_reports_an_empty_radio_when_no_order_stands():
    """`delivered` must track reality, not be hardcoded either way.

    This section used to be hardwired to False because `query_adk_gemini` had no
    directive parameter at all. It does now, so False here has to mean "the
    commander has not radioed anything", and nothing stronger. The delivered
    case is covered in tests/test_commander_radio.py.
    """
    capture = {}
    build_decision_prompt(_observation(), history=[], commander_directive=None, capture=capture)

    commander = capture["sections"]["commander_directive"]
    assert commander["delivered"] is False
    assert commander["text"] not in capture["full_prompt"], (
        "the placeholder text leaked into the real prompt"
    )


def test_full_prompt_is_the_whole_prompt_not_one_section():
    """The Copy button hands out `full_prompt`; it has to be the real thing.

    History is supplied deliberately. With an empty window and no coaching
    notes the prompt collapses to the observation block alone, and a capture
    that recorded only that one section would look correct.
    """
    history = [{
        "tick": 6,
        "observation": {"self": {"position": {"x": 1, "y": 1}}, "score": 2},
        "response": {"action": "east", "thought": "closing"},
    }]
    capture = {}
    prompt = build_decision_prompt(_observation(tick=7), history=history, capture=capture)
    sections = capture["sections"]

    assert capture["full_prompt"] == prompt

    observation = sections["current_observation"]["text"]
    window = sections["history_window"]["text"]
    assert observation in prompt and window in prompt, (
        "a section marked delivered is absent from the prompt"
    )
    assert len(prompt) > len(observation), (
        "full_prompt is no longer than the observation block alone, so it is "
        "recording a fragment rather than everything the model received"
    )


def test_capture_stamps_the_tick_the_prompt_was_built_from():
    capture = {}
    build_decision_prompt(_observation(tick=42), history=[], capture=capture)
    assert capture["built_at_tick"] == 42


def test_system_instruction_section_carries_the_actual_skills_manual():
    """Shipping an empty or placeholder manual here would hide a real bug."""
    capture = {}
    build_decision_prompt(_observation(), history=[], capture=capture)
    text = capture["sections"]["system_instruction"]["text"]
    assert len(text) > 1000, (
        f"the skills manual section is only {len(text)} chars; the real "
        f"actor_skills.md is several KB, so this is not it"
    )


def test_history_section_is_marked_undelivered_when_there_is_no_history():
    """An empty window is a legitimate state and must not read as delivered."""
    capture = {}
    build_decision_prompt(_observation(), history=[], capture=capture)
    assert capture["sections"]["history_window"]["delivered"] is False


def test_history_section_is_delivered_once_ticks_exist():
    history = [{
        "tick": 6,
        "observation": {"self": {"position": {"x": 1, "y": 1}}, "score": 2},
        "response": {"action": "east", "thought": "closing"},
    }]
    capture = {}
    build_decision_prompt(_observation(), history=history, capture=capture)
    assert capture["sections"]["history_window"]["delivered"] is True
    text = capture["sections"]["history_window"]["text"]
    assert "EAST" in text, "the action taken is the point of the history window"
    assert "Tick 6" in text


def test_history_records_the_action_but_not_the_reasoning_behind_it():
    """What happened is evidence. Why it felt right is advocacy, and it is cut.

    Replaying each past thought turned the window into 25 worked examples
    arguing for whatever the cat was already doing, written in its own voice
    and five times the size of the coaching notes trying to correct it.
    Measured over 277 live prompts, the cat then chose the modal action of its
    own history 51.5% of the time against a 16.7% chance baseline -- on free
    ticks, counting only executed actions, so the action lock does not explain
    it.

    The interrogator prompt deliberately keeps its thoughts; it is asked to
    explain itself to the commander, so its reasoning is the subject matter.
    This test guards the decision path only.
    """
    history = [{
        "tick": 6,
        "observation": {"self": {"position": {"x": 1, "y": 1}}, "score": 2},
        "response": {
            "action": "east",
            "thought": "sidling east feels safer than committing to a lunge",
        },
    }]
    prompt = build_decision_prompt(_observation(), history=history)

    assert "EAST" in prompt
    for fragment in ("sidling", "feels safer", "Reason:"):
        assert fragment not in prompt, (
            f"{fragment!r} leaked into the decision prompt; the cat is being "
            f"shown its own justification for the behaviour under correction"
        )


def test_capture_is_optional_and_prompt_building_is_unaffected():
    """Capture is diagnostics; it must never change what the model receives."""
    with_capture = {}
    a = build_decision_prompt(_observation(), history=[], capture=with_capture)
    b = build_decision_prompt(_observation(), history=[], capture=None)
    assert a == b


# --------------------------------------------------------------------------
# Static assets
# --------------------------------------------------------------------------

def test_cachebusters_bumped_for_the_inspector():
    """Both assets changed, so a stale cache must not be served.

    workbench.css previously carried no cachebuster at all, which would have
    left the inspector's styles unapplied on any browser holding the old file.
    """
    html = INDEX_HTML.read_text()
    js = re.search(r"workbench\.js\?v=(\d+)", html)
    css = re.search(r"workbench\.css\?v=(\d+)", html)
    assert js and int(js.group(1)) >= 22, "workbench.js cachebuster not bumped"
    assert css, "workbench.css has no cachebuster; new styles may not load"


def test_inspector_styles_exist():
    css = WORKBENCH_CSS.read_text()
    for cls in (".prompt-meta", ".prompt-section", ".prompt-section-body", ".btn-payload"):
        assert cls in css, f"{cls} is unstyled; the modal will render unreadably"
    assert ".prompt-section.not-delivered" in css, (
        "a section that never reached the model looks identical to one that did"
    )


# --------------------------------------------------------------------------
# JS renderer, executed for real
# --------------------------------------------------------------------------

def _extract_js_function(name: str) -> str:
    src = WORKBENCH_JS.read_text()
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"could not brace-match {name} in workbench.js")


FAKE_DOM = """
function mkEl(tag) {
  return {
    tagName: tag, className: '', textContent: '', innerHTML: '', open: false,
    children: [],
    appendChild: function (c) { this.children.push(c); return c; },
    replaceChildren: function () {
      this.children = Array.prototype.slice.call(arguments);
      this.textContent = '';
    },
    // Real elements have this; a naive fake does not, and cleanup paths throw.
    remove: function () {},
  };
}
var document = { createElement: mkEl };
var promptModalMeta = mkEl('div');
var promptModalSections = mkEl('div');
function dump(el) {
  return { className: el.className, open: el.open,
           text: el.children.map(function (c) { return c.textContent; }) };
}
"""


def _render(record: dict) -> dict:
    program = (
        FAKE_DOM
        + _extract_js_function("renderPromptRecord")
        + f"\nrenderPromptRecord({json.dumps(record)});\n"
        + "print(JSON.stringify({"
        + "  meta: promptModalMeta.children.map(function (m) {"
        + "    return m.children.map(function (c) { return c.textContent; }); }),"
        + "  sections: promptModalSections.children.map(dump),"
        + "}));"
    )
    proc = subprocess.run(
        [str(JSC), "-e", program], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise AssertionError(f"jsc failed: {proc.stderr or proc.stdout}")
    return json.loads(proc.stdout.strip())


jsc_only = pytest.mark.skipif(
    not JSC.exists(), reason="JavaScriptCore helper not available on this platform"
)


def _js_record(**overrides):
    rec = {
        "built_at_tick": 2,
        "applied_at_tick": 3,
        "model": "gemini-2.5-flash",
        "latency_ms": 1313,
        "decision": {"action": "east", "thought": "closing on mouse-42"},
        "sections": {
            "system_instruction": {"label": "Skills manual", "delivered": True, "text": "S" * 50},
            "coaching_notes": {"label": "Coach", "delivered": True, "text": "coach text"},
            "commander_directive": {"label": "Commander", "delivered": False, "text": "NOT DELIVERED."},
            "history_window": {"label": "History", "delivered": True, "text": "hist"},
            "current_observation": {"label": "Observation", "delivered": True, "text": "obs"},
        },
        "full_prompt": "everything",
    }
    rec.update(overrides)
    return rec


@jsc_only
def test_renderer_emits_one_block_per_section_in_pipeline_order():
    out = _render(_js_record())
    labels = [s["text"][0] for s in out["sections"]]
    assert len(labels) == 5
    assert "Skills manual" in labels[0]
    assert "Commander" in labels[2]
    assert "Observation" in labels[4]


@jsc_only
def test_renderer_marks_an_undelivered_section_visually_distinct():
    out = _render(_js_record())
    commander = out["sections"][2]
    assert "not-delivered" in commander["className"], (
        "the commander section renders identically to the delivered ones; the "
        "whole point is that it is visibly absent from the prompt"
    )
    assert "NOT SENT" in commander["text"][0]
    assert commander["open"] is False


@jsc_only
def test_renderer_collapses_the_skills_manual_but_opens_what_varies():
    """The manual is ~7KB and identical every tick; it must not bury the rest."""
    out = _render(_js_record())
    assert out["sections"][0]["open"] is False, "the 7KB skills manual is expanded"
    assert out["sections"][1]["open"] is True, "coaching notes are collapsed"
    assert out["sections"][4]["open"] is True, "the observation is collapsed"


@jsc_only
def test_renderer_calls_out_the_build_versus_apply_lag():
    """The prompt describes an older world than the tick it was applied to.

    This is the off-by-one behind the incoherent trajectory, so the modal has to
    state it rather than let the reader assume the prompt matches the row.
    """
    out = _render(_js_record(built_at_tick=2, applied_at_tick=3))
    line = out["meta"][0][0]
    assert "tick 2" in line and "1 tick(s) later" in line, (
        f"the build/apply lag is not surfaced: {line!r}"
    )


@jsc_only
def test_renderer_says_so_when_there_is_no_lag():
    out = _render(_js_record(built_at_tick=5, applied_at_tick=5))
    assert "same tick" in out["meta"][0][0]


@jsc_only
def test_renderer_survives_a_missing_section():
    """Older records predate any section added later; the modal must still open."""
    rec = _js_record()
    del rec["sections"]["coaching_notes"]
    out = _render(rec)
    assert len(out["sections"]) == 4


@jsc_only
def test_renderer_uses_textcontent_so_prompt_text_cannot_inject_markup():
    """Prompt text is model- and user-influenced and lands in the DOM verbatim."""
    rec = _js_record()
    rec["sections"]["coaching_notes"]["text"] = "<img src=x onerror=alert(1)>"
    body = re.sub(r"//.*", "", _extract_js_function("renderPromptRecord"))
    assert "innerHTML" not in body, (
        "renderPromptRecord assigns innerHTML; prompt text would be parsed as "
        "markup"
    )
    out = _render(rec)
    assert len(out["sections"]) == 5


def test_payload_button_is_only_offered_for_gemini_ticks():
    """A reflex tick has no prompt, so the button would promise nothing."""
    src = WORKBENCH_JS.read_text()
    body = re.sub(r"//.*", "", src)
    assert "item.source === 'adk_gemini'" in body, (
        "the Payload button is no longer gated on the decision source; it "
        "would appear on reflex rows that can never have a prompt"
    )


def test_copy_button_copies_the_verbatim_prompt():
    """Re-joining the rendered sections would not reproduce what Gemini saw."""
    body = re.sub(r"//.*", "", WORKBENCH_JS.read_text())
    assert "writeText(lastPromptRecord.full_prompt)" in body, (
        "the copy handler no longer hands out full_prompt verbatim"
    )

