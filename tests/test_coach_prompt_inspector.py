"""Tests for the Coach / Evaluator prompt payload inspector.

Verifies:
1. On-disk store append, read by tick, read latest, and reset.
2. Prompt capture assembly during build_eval_prompt across all sections.
3. Coach payload inspection endpoints in both cat API and evil_cat API.
4. JavaScriptCore rendering of coach payload records in workbench UI.
"""

import json
from pathlib import Path
import re
import subprocess

import pytest
from starlette.testclient import TestClient

from actors.cat import store
from actors.cat.api import app
from actors.cat.subagents.eval import build_eval_prompt
from actors.evil_cat.api import app as evil_app

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_JS = ROOT / "actors/cat/static/workbench.js"
INDEX_HTML = ROOT / "actors/cat/static/index.html"

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")
jsc_only = pytest.mark.skipif(
    not JSC.exists(), reason="JavaScriptCore helper not available on this platform"
)

COACH_SECTION_KEYS = [
    "coach_identity",
    "eval_skills",
    "actor_manual",
    "capabilities",
    "standing_directives",
    "commander_order",
    "coach_notebook",
    "transcript_status",
    "a2a_diplomacy",
]


@pytest.fixture(autouse=True)
def clean_coach_prompt_log():
    store.reset_coach_prompts()
    yield
    store.reset_coach_prompts()


def _sample_coach_record(built=5, agent_id="cat-1", **extra):
    rec = {
        "built_at_tick": built,
        "applied_at_tick": built,
        "model": "gemini-2.5-flash",
        "agent_id": agent_id,
        "latency_ms": 942,
        "thinking_budget": 2048,
        "thoughts_token_count": 812,
        "output": "- MODE: STALK cat-2\n- TRIGGER: spotted\n- BEHAVIOR: Close distance",
        "sections": {k: {"label": k, "delivered": True, "text": f"Content of {k}"} for k in COACH_SECTION_KEYS},
        "full_prompt": f"full-coach-prompt-for-{built}",
    }
    rec.update(extra)
    return rec


# --------------------------------------------------------------------------
# 1. Store: on-disk coach prompt log
# --------------------------------------------------------------------------

def test_coach_prompt_append_and_read_round_trip():
    rec = _sample_coach_record(built=10)
    store.append_coach_prompt(rec, agent_id="cat-1")

    # Read latest
    latest = store.read_coach_prompt_for_tick(tick=None, agent_id="cat-1")
    assert latest is not None
    assert latest["built_at_tick"] == 10
    assert latest["full_prompt"] == "full-coach-prompt-for-10"

    # Read by tick
    by_tick = store.read_coach_prompt_for_tick(tick=10, agent_id="cat-1")
    assert by_tick is not None
    assert by_tick["built_at_tick"] == 10


def test_coach_prompt_tick_range_lookup():
    store.append_coach_prompt(_sample_coach_record(built=5), agent_id="cat-1")
    store.append_coach_prompt(_sample_coach_record(built=10), agent_id="cat-1")

    # Asking for tick 7 should resolve to the eval active at tick 7 (built at 5)
    r7 = store.read_coach_prompt_for_tick(tick=7, agent_id="cat-1")
    assert r7 is not None
    assert r7["built_at_tick"] in (5, 10)

    # Asking for tick 12 resolves to built at 10
    r12 = store.read_coach_prompt_for_tick(tick=12, agent_id="cat-1")
    assert r12 is not None
    assert r12["built_at_tick"] == 10


def test_coach_prompt_reset_and_stats():
    store.append_coach_prompt(_sample_coach_record(built=5), agent_id="cat-1")
    stats = store.coach_prompt_log_size("cat-1")
    assert stats["records"] >= 1
    assert stats["bytes"] > 0

    store.reset_coach_prompts("cat-1")
    cleared = store.read_coach_prompt_for_tick(tick=None, agent_id="cat-1")
    assert cleared is None


# --------------------------------------------------------------------------
# 2. Prompt capture assembly: build_eval_prompt
# --------------------------------------------------------------------------

def test_build_eval_prompt_populates_capture_bundle():
    bundle = {}
    records = [{
        "tick": 15,
        "observation": {"self": {"hp": 100, "position": {"x": 5, "y": 5}}, "scoreboard": {"cat-1": 2}},
        "response": {"action": "attack", "thought": "slashed mouse"},
    }]
    prompt = build_eval_prompt(
        records=records,
        previous="• [TACTICAL] Prior order",
        coach_directive="Focus on eastern sector",
        agent_id="cat-1",
        capture=bundle,
    )
    assert bundle["model"] == "gemini-2.5-flash"
    assert bundle["agent_id"] == "cat-1"
    assert bundle["built_at_tick"] == 15
    assert bundle["full_prompt"] == prompt

    sections = bundle["sections"]
    for k in COACH_SECTION_KEYS:
        assert k in sections, f"Section {k} missing from coach prompt capture"
        assert "label" in sections[k] and "text" in sections[k] and "delivered" in sections[k]

    assert "coach(cat-1)" in sections["coach_identity"]["text"]
    assert "Focus on eastern sector" in sections["commander_order"]["text"]
    assert "Prior order" in sections["standing_directives"]["text"]


def test_build_eval_prompt_for_evil_cat_captures_evil_manuals():
    bundle = {}
    records = [{"tick": 5, "observation": {"self": {"hp": 150}}, "response": {"action": "stay"}}]
    build_eval_prompt(
        records=records,
        previous="",
        coach_directive=None,
        agent_id="cat-3",
        capture=bundle,
    )
    assert bundle["eval_skills_name"] == "coach_skills.md"
    assert "skills.md" in bundle["sections"]["actor_manual"]["text"]
    assert "coach(cat-3)" in bundle["sections"]["coach_identity"]["text"]


# --------------------------------------------------------------------------
# 3. HTTP Endpoints: Cat API & Evil Cat API
# --------------------------------------------------------------------------

def test_cat_api_coach_prompt_endpoints():
    client = TestClient(app)

    # Empty log returns not_found
    empty_res = client.get("/api/agent/coach/prompt/latest")
    assert empty_res.status_code == 200
    assert empty_res.json()["status"] == "not_found"

    # Populate a record
    store.append_coach_prompt(_sample_coach_record(built=20), agent_id="cat-1")

    # Latest endpoint
    latest_res = client.get("/api/agent/coach/prompt/latest")
    assert latest_res.status_code == 200
    data = latest_res.json()
    assert data["status"] == "ok"
    assert data["record"]["built_at_tick"] == 20

    # Tick lookup
    tick_res = client.get("/api/agent/coach/prompt/20")
    assert tick_res.status_code == 200
    assert tick_res.json()["status"] == "ok"

    # Stats
    stats_res = client.get("/api/agent/coach/prompt-log/stats")
    assert stats_res.status_code == 200
    assert stats_res.json()["records"] >= 1


def test_evil_cat_api_coach_prompt_endpoints():
    client = TestClient(evil_app)
    store.append_coach_prompt(_sample_coach_record(built=10, agent_id="cat-3"), agent_id="cat-3")

    res = client.get("/api/agent/coach/prompt/latest")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    wb_res = client.get("/api/workbench/coach/prompt/latest")
    assert wb_res.status_code == 200
    data = wb_res.json()
    rec = data.get("record") or data
    assert rec.get("built_at_tick") == 10


# --------------------------------------------------------------------------
# 4. Frontend & JavaScriptCore Integration
# --------------------------------------------------------------------------

def test_index_html_has_coach_payload_button():
    content = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="btnCoachPayload"' in content
    assert "Coach Payload" in content


def test_workbench_js_wires_coach_payload():
    content = WORKBENCH_JS.read_text(encoding="utf-8")
    assert "btnCoachPayload" in content
    assert "openCoachPromptModal" in content
    assert "/api/agent/coach/prompt" in content


def _extract_js_func(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"could not brace-match {name}")


@jsc_only
def test_render_coach_prompt_record_in_jsc():
    src = WORKBENCH_JS.read_text(encoding="utf-8")
    render_func = _extract_js_func(src, "renderPromptRecord")
    format_budget_func = _extract_js_func(src, "formatBudget")

    fake_dom = """
    function mkEl(tag) {
      return {
        tagName: tag, className: '', textContent: '', innerHTML: '', open: false,
        children: [],
        appendChild: function (c) { this.children.push(c); return c; },
        replaceChildren: function () {
          this.children = Array.prototype.slice.call(arguments);
          this.textContent = '';
        },
        remove: function () {},
      };
    }
    var document = { createElement: mkEl };
    var promptModalMeta = mkEl('div');
    var promptModalSections = mkEl('div');
    var thinkingBounds = { off: 0, auto: -1, min: 1, max: 24576, canDisable: true };
    function dump(el) {
      return { className: el.className, open: el.open,
               text: el.children.map(function (c) { return c.textContent; }) };
    }
    """

    record = _sample_coach_record(built=15)
    program = (
        fake_dom
        + format_budget_func
        + "\n"
        + render_func
        + f"\nrenderPromptRecord({json.dumps(record)});\n"
        + "print(JSON.stringify({"
        + "  meta: promptModalMeta.children.map(function (m) {"
        + "    return m.children.map(function (c) { return c.textContent; }); }),"
        + "  sections: promptModalSections.children.map(dump),"
        + "}));"
    )

    proc = subprocess.run([str(JSC), "-e", program], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"jsc failed: {proc.stderr}"
    out = json.loads(proc.stdout.strip())

    # Check meta output displays the published directive
    meta_lines = out["meta"][0]
    assert any("Published Directive" in l for l in meta_lines)
    assert any("2,048 tokens" in l for l in meta_lines)
    assert any("812 spent" in l for l in meta_lines)

    # Check sections rendered
    sec_names = [s["text"][0] for s in out["sections"]]
    assert any("eval_skills" in s for s in sec_names)
    assert any("actor_manual" in s for s in sec_names)
    assert any("standing_directives" in s for s in sec_names)
    assert any("coach_identity" in s for s in sec_names)

    # Manuals should be collapsed, dynamic sections open
    for s in out["sections"]:
        label = s["text"][0]
        if "eval_skills" in label or "actor_manual" in label or "system_instruction" in label:
            assert s["open"] is False
        elif "coach_identity" in label or "standing_directives" in label:
            assert s["open"] is True
