import ast
from pathlib import Path
import pytest
from actors.models import Observation, CatTacticalDecision
from actors.evil_cat.actor import build_decision_prompt, adk_agent, get_skills_instruction
from actors.evil_cat.eval import (
    EVAL_TIMEOUT_MAX_SEC,
    build_coach_eval_prompt,
    coerce_eval_timeout,
    eval_agent,
    get_eval_instruction,
    get_eval_timeout,
)
from actors.evil_cat.heuristic import compute_heuristic_decision, compute_reflex_decision


def test_evil_cat_has_zero_imports_from_regular_cat():
    """Evil Cat (`actors/evil_cat/`) must be completely decoupled from regular cat (`actors/cat/`)."""
    evil_dir = Path(__file__).resolve().parents[1] / "actors" / "evil_cat"
    py_files = sorted(evil_dir.glob("*.py"))
    assert len(py_files) >= 5

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("actors.cat"), (
                        f"{py_file.name} imports {alias.name}; actors/evil_cat must not depend on actors/cat"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("actors.cat"), (
                    f"{py_file.name} imports from {mod}; actors/evil_cat must not depend on actors/cat"
                )


def test_evil_actor_has_no_tools_and_clean_schema():
    """Evil Cat Fast Brain must have zero tools to eliminate tool-calling latency."""
    assert adk_agent.tools == [] or len(adk_agent.tools) == 0
    skills = get_skills_instruction("cat-3")
    assert "PRIMARY OBJECTIVE: Follow the Evil Coach's Standing Directive" in skills
    assert "150 Max HP" in skills
    assert "Double Bounty" in skills


def test_evil_actor_prompt_focuses_on_standing_directive():
    obs = Observation(
        tick=10,
        round=1,
        self={"id": "cat-3", "position": {"x": 12, "y": 8}, "facing": "east", "hp": 150, "max_hp": 150},
        legal_actions=["stay", "north", "south", "east", "west", "attack"],
        scoreboard={"cat-1": 10, "cat-2": 5, "cat-3": 0},
    )
    capture = {}
    prompt = build_decision_prompt(obs=obs, capture=capture, agent_id="cat-3")

    assert "EVALUATOR NOTES (PRIMARY OBJECTIVE: STANDING DIRECTIVE)" in prompt
    assert "CURRENT OBSERVATION" in prompt
    assert "150 HP Tank" in prompt
    assert "cat-1: 10 score [ALIVE - 2x Bounty: +20 pts]" in prompt
    assert "COMMANDER RADIO" not in prompt
    assert "INTER-CAT RADIO" not in prompt


def test_evil_coach_has_a2a_tools_and_emits_standing_order_pattern():
    """Evil Coach must retain A2A tools for deep scheming and social warfare."""
    tool_names = [getattr(t, "__name__", str(t)) for t in eval_agent.tools]
    assert "a2a_broadcast" in tool_names or any("broadcast" in n for n in tool_names)
    assert "a2a_send_coach_message" in tool_names or any("coach_message" in n for n in tool_names)

    instructions = get_eval_instruction()
    assert "Evil Coach" in instructions
    assert "MODE:" in instructions
    assert "TRIGGER:" in instructions
    assert "BEHAVIOR:" in instructions

    # Test prompt formatting
    records = [{"tick": 1, "observation": {"self": {"hp": 150}, "scoreboard": {"cat-1": 8}}}]
    coach_prompt = build_coach_eval_prompt(records, agent_id="cat-3")
    assert "Scoreboard & Bounties (2x value when you kill them)" in coach_prompt
    assert "MODE:" in coach_prompt


def test_evil_cat_heuristic_and_reflex_never_chase_mice_by_default():
    """Evil Cat heuristic and reflex (in actors/evil_cat/heuristic.py) must ignore mice and patrol or hunt rival cats."""
    # Scenario 1: Mouse right in strike zone, no rival cat visible -> Evil Cat ignores mouse and patrols
    obs_mouse_only = Observation(
        tick=5,
        round=1,
        self={"id": "cat-3", "position": {"x": 5, "y": 5}, "facing": "east", "hp": 150, "max_hp": 150},
        visible_entities=[
            {"id": "mouse-1", "type": "mouse", "position": {"x": 6, "y": 5}},
        ],
        legal_actions=["stay", "north", "south", "east", "west", "attack", "lunge"],
        capabilities={"attack_range": 2},
    )
    h_act, h_thought, h_meta = compute_heuristic_decision(obs_mouse_only)
    assert h_act != "attack"
    assert h_meta.get("target_id") != "mouse-1"

    r_act, r_thought, r_meta = compute_reflex_decision(obs_mouse_only)
    assert r_act != "attack"
    assert r_meta.get("target_id") != "mouse-1"

    # Scenario 2: Rival cat in strike zone alongside a mouse -> Evil Cat slashes the rival cat
    obs_rival_in_zone = Observation(
        tick=6,
        round=1,
        self={"id": "cat-3", "position": {"x": 5, "y": 5}, "facing": "east", "hp": 150, "max_hp": 150},
        visible_entities=[
            {"id": "mouse-1", "type": "mouse", "position": {"x": 6, "y": 5}},
            {"id": "cat-1", "type": "cat", "position": {"x": 7, "y": 5}},
        ],
        scoreboard={"cat-1": 12, "cat-2": 4, "cat-3": 0},
        legal_actions=["stay", "north", "south", "east", "west", "attack", "lunge"],
        capabilities={"attack_range": 2},
    )
    h_act2, _, h_meta2 = compute_heuristic_decision(obs_rival_in_zone)
    assert h_act2 == "attack"
    assert h_meta2["target_id"] == "cat-1"

    r_act2, _, r_meta2 = compute_reflex_decision(obs_rival_in_zone)
    assert r_act2 == "attack"
    assert r_meta2["target_id"] == "cat-1"

    # Scenario 3: Multiple rival cats visible -> Evil Cat prioritizes higher bounty rival cat
    obs_two_rivals = Observation(
        tick=7,
        round=1,
        self={"id": "cat-3", "position": {"x": 5, "y": 5}, "facing": "north", "hp": 150, "max_hp": 150},
        visible_entities=[
            {"id": "cat-1", "type": "cat", "position": {"x": 2, "y": 5}},
            {"id": "cat-2", "type": "cat", "position": {"x": 8, "y": 5}},
        ],
        scoreboard={"cat-1": 2, "cat-2": 20, "cat-3": 0},
        legal_actions=["stay", "north", "south", "east", "west", "attack"],
        capabilities={"attack_range": 2},
    )
    h_act3, _, h_meta3 = compute_heuristic_decision(obs_two_rivals)
    assert h_act3 == "east"
    assert h_meta3["target_id"] == "cat-2"


def test_evil_coach_timeout_has_no_900s_cap():
    """Evil Coach must have no 900s cap on thinking/evaluation timeout."""
    assert get_eval_timeout() is None
    assert EVAL_TIMEOUT_MAX_SEC is None
    assert coerce_eval_timeout(None) is None
    assert coerce_eval_timeout("none") is None
    assert coerce_eval_timeout(0) is None
    assert coerce_eval_timeout(3600) == 3600.0
    assert coerce_eval_timeout(10000) == 10000.0


def test_evil_cat_api_standalone_endpoints_and_unlimited_timeout():
    """Evil Cat API (`actors.evil_cat.api`) serves its own workbench, `/act`, and unlimited coach timeout."""
    from starlette.testclient import TestClient
    from actors.evil_cat.api import app as evil_app

    client = TestClient(evil_app)

    # Check evaluator thinking budget endpoint reports unlimited timeout (None) by default
    res = client.get("/api/agent/evaluator/thinking-budget")
    assert res.status_code == 200
    data = res.json()
    assert data["timeout_max_sec"] is None

    # Setting a timeout > 900s (e.g., 7200s) succeeds on Evil Coach
    post_res = client.post(
        "/api/agent/evaluator/thinking-budget",
        json={"thinking_budget": 4096, "eval_timeout_sec": 7200},
    )
    assert post_res.status_code == 200
    assert post_res.json()["timeout_sec"] == 7200.0

    # Setting 0 resets Evil Coach timeout back to unlimited (None)
    reset_res = client.post(
        "/api/agent/evaluator/thinking-budget",
        json={"thinking_budget": 1024, "eval_timeout_sec": 0},
    )
    assert reset_res.status_code == 200
    assert reset_res.json()["timeout_sec"] is None


def test_evaluator_keeps_previous_notes_when_returning_empty_notes(monkeypatch, tmp_path):
    """If the evaluator returns empty notes, it must keep the previous note as before."""
    import asyncio
    from actors.evil_cat import eval as evil_eval, store as evil_store
    from actors.cat.subagents import eval as cat_eval
    from actors.cat import store as cat_store

    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))

    async def _empty_agen(*_args, **_kwargs):
        if False:
            yield None

    # 1. Evil Coach evaluator
    prior_evil_note = "MODE: AMBUSH\nTRIGGER: cat-1 within 2 tiles -> slash\nBEHAVIOR: Hold (45, 50) facing North."
    evil_store.write_coaching_notes(prior_evil_note, tick_range="1-20", agent_id="cat-3")
    monkeypatch.setattr(evil_eval, "has_gemini_credentials", lambda: True)
    monkeypatch.setattr(evil_eval.eval_runner, "run_async", _empty_agen)

    notes, source = asyncio.run(
        evil_eval.evaluate([{"tick": 25, "observation": {"self": {"hp": 150}}}], agent_id="cat-3")
    )
    assert source == "retained_previous"
    assert notes == prior_evil_note
    assert evil_store.read_coaching_notes(agent_id="cat-3") == prior_evil_note

    # 2. Regular Cat evaluator
    prior_cat_note = "- **Flank East**: Sweep column x=80 southward."
    cat_store.write_coaching_notes(prior_cat_note, tick_range="1-20", agent_id="cat-1")
    monkeypatch.setenv("ENABLE_LIVE_LLM_TESTS", "1")
    monkeypatch.setattr(cat_eval, "has_gemini_credentials", lambda: True)
    monkeypatch.setattr(cat_eval.eval_runner, "run_async", _empty_agen)

    c_notes, c_source = asyncio.run(
        cat_eval.evaluate([{"tick": 25, "x": 10, "y": 10}], agent_id="cat-1")
    )
    assert c_source == "retained_previous"
    assert c_notes == prior_cat_note
    assert cat_store.read_coaching_notes(agent_id="cat-1") == prior_cat_note


def test_evil_cat_act_async_consumes_deliberated_decision_and_appends_tick(monkeypatch, tmp_path):
    """Ensure Evil Cat's act_async records prompts and ticks with agent_id without raising TypeError."""
    import asyncio
    from actors.models import CatTacticalDecision, Observation
    from actors.evil_cat import api as evil_api, store as evil_store

    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(evil_api, "check_gemini_auth_health", lambda: {"authenticated": True})
    monkeypatch.setattr(evil_api, "maybe_dispatch_evaluation", lambda *_a, **_kw: None)

    evil_api.state.latest_deliberated_decision = CatTacticalDecision(
        strategy="STALKING",
        target_id="cat-1",
        target_mouse_id="cat-1",
        thought="Closing distance on cat-1 for ambush.",
        action="lunge",
    )
    evil_api.state.latest_deliberated_prompt = {"built_at_tick": 10, "user_prompt": "test"}
    evil_api.state.is_deliberating = True

    obs = Observation(
        tick=11,
        round=1,
        grid={"width": 100, "height": 100},
        self={"id": "cat-3", "position": {"x": 20, "y": 20}, "facing": "east", "hp": 150, "alive": True},
        visible_entities=[],
        legal_actions=["stay", "north", "south", "east", "west", "lunge", "slash"],
    )

    resp = asyncio.run(evil_api.act_async(obs, decoupled=True))
    assert resp.action == "lunge"
    assert evil_api.state.last_engine_source == "adk_gemini"
    assert evil_store.transcript_tick_count(agent_id="cat-3") == 1
    assert evil_store.read_prompt_for_tick(11, agent_id="cat-3") is not None
