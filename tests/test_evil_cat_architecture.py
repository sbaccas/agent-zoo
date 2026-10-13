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
    assert "Kill Absorption" in skills


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
    assert "cat-1: 10 score [ALIVE - Bounty: +10 pts]" in prompt
    assert "COMMANDER RADIO" not in prompt
    assert "INTER-CAT RADIO" not in prompt


def test_evil_coach_has_a2a_tools_and_emits_standing_order_pattern():
    """Evil Coach must retain A2A tools for deep scheming and keep instructions strictly in Markdown."""
    tool_names = [getattr(t, "__name__", str(t)) for t in eval_agent.tools]
    assert "a2a_broadcast" in tool_names or any("broadcast" in n for n in tool_names)
    assert "a2a_send_coach_message" in tool_names or any("coach_message" in n for n in tool_names)

    instructions = get_eval_instruction()
    assert "Evil Coach" in instructions
    assert "MODE:" in instructions
    assert "TRIGGER:" in instructions
    assert "BEHAVIOR:" in instructions

    # Prompt builder emits data only (no raw YOUR MISSION instruction block in Python)
    records = [{"tick": 1, "observation": {"self": {"hp": 150}, "scoreboard": {"cat-1": 8}}}]
    coach_prompt = build_coach_eval_prompt(records, agent_id="cat-3")
    assert "Scoreboard & Bounties (absorbed when you kill them):" in coach_prompt
    assert "--- LAST 10 TICKS: TACTICAL HISTORY & REASONING ---" in coach_prompt
    assert "--- LAST 10 TICKS: OBSERVATION PAYLOADS ---" in coach_prompt
    assert "--- LATEST ACTOR PAYLOAD (EXACT PROMPT FACING SUBORDINATE) ---" in coach_prompt
    assert "--- YOUR SUBORDINATE'S MANUAL (skills.md) ---" in coach_prompt
    assert "YOUR MISSION:" not in coach_prompt
    assert "MODE: <MODE_NAME>" not in coach_prompt


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


def test_evil_cat_multimodal_viewport_png_and_cardinal_legend(monkeypatch, tmp_path):
    """Verify cat-3 renders a <=384x384 PNG viewport cutout and attaches image/png + text parts to Gemini ADK."""
    import asyncio
    import struct
    from types import SimpleNamespace
    from actors.models import CatTacticalDecision, Observation
    from actors.evil_cat import actor as evil_actor
    from actors.evil_cat.vision import (
        format_cardinal_target_legend,
        render_viewport_data_uri,
        render_viewport_png,
    )

    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))

    obs = Observation(
        tick=42,
        round=6,
        grid={"width": 100, "height": 100},
        self={
            "id": "cat-3",
            "position": {"x": 50, "y": 43},
            "facing": "east",
            "hp": 150,
            "max_hp": 150,
            "alive": True,
        },
        capabilities={"vision_radius": 4, "vision_rear": 2, "vision_half_width": 5, "attack_range": 4},
        scoreboard={"cat-3": 30, "cat-9": 44, "cat-2": 18},
        visible_entities=[
            {
                "id": "cat-9",
                "type": "cat",
                "position": {"x": 53, "y": 43},
                "hp": 66,
                "max_hp": 100,
                "forward": 3,
                "offaxis": 0,
                "in_strike_zone": True,
            },
            {
                "id": "mouse-12",
                "type": "mouse",
                "position": {"x": 53, "y": 40},
                "forward": 3,
                "offaxis": -3,
                "in_strike_zone": False,
            },
        ],
        legal_actions=["stay", "north", "south", "east", "west", "lunge", "attack"],
    )

    # 1. Verify PNG header and single-tile Gemini dimensions (<= 384x384)
    png_bytes, data_uri = render_viewport_data_uri(obs, agent_id="cat-3")
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert data_uri.startswith("data:image/png;base64,")
    width, height = struct.unpack(">II", png_bytes[16:24])
    assert 0 < width <= 384
    assert 0 < height <= 384

    # Check all 4 facings render cleanly including near wall boundaries
    for facing in ("north", "south", "east", "west"):
        wall_obs = Observation(
            tick=43,
            round=6,
            grid={"width": 100, "height": 100},
            self={"id": "cat-3", "position": {"x": 1, "y": 98}, "facing": facing, "hp": 150, "alive": True},
            visible_entities=[],
            legal_actions=["stay", "north", "east"],
        )
        b = render_viewport_png(wall_obs, agent_id="cat-3")
        assert b.startswith(b"\x89PNG\r\n\x1a\n")

    # 2. Verify Cardinal Target Legend output
    legend = format_cardinal_target_legend(obs, agent_id="cat-3")
    assert "[C9] cat-9" in legend
    assert "3 EAST, 0 N/S" in legend
    assert "IN STRIKE ZONE" in legend
    assert "[M12] mouse-12" in legend
    assert "3 EAST, 3 NORTH" in legend
    assert "step NORTH/EAST" in legend

    # 3. Verify query_adk_gemini sends multimodal image/png Part + text Part and captures image_data_uri
    captured_messages = []

    async def _fake_run_async(*, user_id, session_id, new_message):
        captured_messages.append(new_message)
        yield SimpleNamespace(
            error_code=None,
            error_message=None,
            usage_metadata=SimpleNamespace(thoughts_token_count=64),
            actions=SimpleNamespace(
                state_delta={
                    "decision": CatTacticalDecision(
                        strategy="ATTACK",
                        target_id="cat-9",
                        target_mouse_id="cat-9",
                        thought="cat-9 is locked in strike zone 3 tiles East; attacking for +88 bounty.",
                        action="attack",
                    )
                }
            ),
            content=None,
        )

    monkeypatch.setattr(evil_actor, "has_gemini_credentials", lambda: True)
    monkeypatch.setattr(evil_actor.adk_runner, "run_async", _fake_run_async)

    capture_bundle = {}
    decision, source, err = asyncio.run(
        evil_actor.query_adk_gemini(obs, agent_id="cat-3", capture=capture_bundle)
    )
    assert err is None
    assert source == "adk_gemini"
    assert decision is not None and decision.action == "attack"
    assert len(captured_messages) == 1

    msg = captured_messages[0]
    assert len(msg.parts) == 2
    assert msg.parts[0].inline_data.mime_type == "image/png"
    assert msg.parts[0].inline_data.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert "Visible Targets in Sight (2)" in msg.parts[1].text

    vp_sec = capture_bundle["sections"]["viewport_image"]
    assert vp_sec["delivered"] is True
    assert vp_sec["image_data_uri"].startswith("data:image/png;base64,")


def test_all_evil_coach_tools_are_async_and_broadcast_never_self_posts(monkeypatch):
    """All Evil Coach tools must be async coroutines and A2A broadcast must never self-POST to :8005."""
    import asyncio
    import inspect
    from types import SimpleNamespace
    from actors.a2a import client as a2a_client

    for tool_fn in eval_agent.tools:
        assert inspect.iscoroutinefunction(tool_fn), (
            f"Evil Coach tool {getattr(tool_fn, '__name__', tool_fn)} must be async def so it never blocks the event loop"
        )

    posted_urls = []

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            posted_urls.append(url)
            return SimpleNamespace(status_code=200, text="ok", json=lambda: {"status": "delivered"})

    monkeypatch.setattr(a2a_client.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(a2a_client, "_report_transmission_to_engine", lambda *a, **kw: asyncio.sleep(0))

    resp = asyncio.run(
        a2a_client.send_a2a_message(
            sender_id="coach(cat-3)",
            recipient_id="*",
            content="Testing non-blocking broadcast",
            message_type="BROADCAST",
        )
    )
    assert resp.status == "delivered"
    # Must only hit cat-1 (:8001) and cat-2 (:8003), never self-POST to :8005 (cat-3 / evil-cat)
    assert all("8005" not in u for u in posted_urls), f"Broadcast self-posted to :8005: {posted_urls}"
    assert len(posted_urls) == len(set(posted_urls)), f"Duplicate broadcast POSTs: {posted_urls}"


def test_check_gemini_auth_health_reuses_credentials_object_across_ttl(monkeypatch):
    """check_gemini_auth_health must reuse google.auth Credentials across 5s TTL expirations so it does not refresh OAuth every 5s."""
    import google.auth
    from actors.cat import actor as cat_actor
    from actors.evil_cat import actor as evil_actor

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "catz-app-prod")

    default_calls = 0
    refresh_calls = 0

    class _FakeCreds:
        def __init__(self):
            self.valid = False

        def refresh(self, _req):
            nonlocal refresh_calls
            refresh_calls += 1
            self.valid = True

    def _fake_default():
        nonlocal default_calls
        default_calls += 1
        return _FakeCreds(), "catz-app-prod"

    monkeypatch.setattr(google.auth, "default", _fake_default)

    for mod in (evil_actor, cat_actor):
        default_calls = 0
        refresh_calls = 0
        mod._auth_cache["timestamp"] = 0.0
        mod._auth_cache["result"] = None
        mod._auth_cache["credentials"] = None
        mod._auth_cache["detected_project"] = None

        # 1. Cold check: loads default() once and refreshes once
        res1 = mod.check_gemini_auth_health()
        assert res1["authenticated"] is True
        assert default_calls == 1
        assert refresh_calls == 1

        # 2. Expire the 5-second result cache TTL: must reuse in-memory valid creds with 0 refreshes!
        mod._auth_cache["timestamp"] = 0.0
        res2 = mod.check_gemini_auth_health()
        assert res2["authenticated"] is True
        assert default_calls == 1, f"{mod.__name__} re-called google.auth.default() when creds were already valid"
        assert refresh_calls == 1, f"{mod.__name__} re-called creds.refresh() when creds.valid was True"

        # 3. Explicit force_refresh=True reloads from disk
        res3 = mod.check_gemini_auth_health(force_refresh=True)
        assert res3["authenticated"] is True
        assert default_calls == 2
        assert refresh_calls == 2


def test_evil_cat_observation_payload_matches_standard_cat_capabilities():
    """Evil Cat prompt includes the same Capabilities block as standard cats without movement instructions."""
    obs = Observation(
        tick=360,
        round=2,
        self={
            "id": "cat-3",
            "position": {"x": 35, "y": 35},
            "facing": "west",
            "hp": 150,
            "max_hp": 150,
            "action_cooldown": 0,
            "can_act": True,
        },
        visible_entities=[
            {"id": "cat-2", "type": "cat", "position": {"x": 28, "y": 33}, "alive": True},
        ],
        legal_actions=["stay", "north", "south", "east", "west", "attack", "lunge"],
        capabilities={"attack_range": 4, "lunge_cooldown": 2, "move_cooldown": 3, "attack_cooldown": 3},
        scoreboard={"cat-2": 31, "cat-3": 50},
    )
    prompt = build_decision_prompt(obs=obs, agent_id="cat-3")
    assert "Capabilities this tick:" in prompt
    assert "• move_cooldown: 3 tick(s)" in prompt
    assert "• attack_cooldown: 3 tick(s)" in prompt
    assert "• lunge_cooldown: 2 tick(s)" in prompt
    assert "• attack_range: 4 tiles deep, strike zone 3 tiles wide" in prompt


def test_standing_directive_pydantic_enforcement_and_markdown_partition(monkeypatch, tmp_path):
    """Verify StandingDirective Pydantic model strictly enforces MODE / TRIGGER / BEHAVIOR and manuals live in Markdown."""
    from pydantic import ValidationError
    from actors.models import StandingDirective
    from actors.evil_cat import actor as evil_actor, eval as evil_eval, store as evil_store

    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))

    # 1. Valid raw text (with leading narration and markdown fences) parses cleanly into StandingDirective
    raw_output = (
        "Here is my updated strategic directive:\n"
        "```text\n"
        "MODE: stalk\n"
        "TRIGGER: cat-1 in strike zone -> attack immediately\n"
        "BEHAVIOR: Turn West and lunge continuously toward (12, 40).\n"
        "```\n"
    )
    directive = StandingDirective.from_text(raw_output)
    assert directive.mode == "STALK"
    assert directive.trigger == "cat-1 in strike zone -> attack immediately"
    assert directive.behavior == "Turn West and lunge continuously toward (12, 40)."
    assert directive.to_block() == (
        "MODE: STALK\n"
        "TRIGGER: cat-1 in strike zone -> attack immediately\n"
        "BEHAVIOR: Turn West and lunge continuously toward (12, 40)."
    )

    # 2. Missing fields raise ValidationError
    with pytest.raises(ValidationError):
        StandingDirective.from_text("MODE: STALK\nBEHAVIOR: Only two fields provided.")

    with pytest.raises(ValidationError):
        StandingDirective.from_text("Just freeform prose without any headers.")

    # 3. store.write_coaching_notes enforces StandingDirective validation
    with pytest.raises(ValidationError):
        evil_store.write_coaching_notes("Invalid prose note", agent_id="cat-3")

    saved = evil_store.write_coaching_notes(raw_output, tick_range="1-10", agent_id="cat-3")
    assert isinstance(saved, StandingDirective)
    assert evil_store.read_coaching_notes(agent_id="cat-3") == directive.to_block()
    assert evil_store.read_standing_directive(agent_id="cat-3") == directive

    # 4. Evil Cat actor has no radio/interrogator agent (cannot talk back) and manuals live in Markdown
    assert not hasattr(evil_actor, "adk_interrogator_agent")
    assert not hasattr(evil_actor, "interrogate_adk_gemini")
    assert evil_actor.SKILLS_PATH.exists()
    assert evil_eval.COACH_SKILLS_PATH.exists()
    assert evil_eval.GENERAL_RADIO_SKILLS_PATH.exists()
    assert evil_eval.get_general_radio_instruction().strip() in evil_eval._general_radio_instruction_provider(None)


def test_coach_eval_prompt_includes_last_10_history_observations_and_latest_actor_payload(monkeypatch, tmp_path):
    """Verify Evil Coach receives last 10 history/reasoning, last 10 observation payloads, and latest actor payload."""
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))

    records = []
    for i in range(1, 16):
        records.append({
            "tick": i,
            "x": i,
            "y": 0,
            "facing": "east",
            "action": "lunge" if i % 2 == 0 else "east",
            "thought": f"Reasoning on tick {i}",
            "source": "adk_gemini",
            "score": i,
            "observation": {
                "tick": i,
                "round": 1,
                "self": {
                    "id": "cat-3",
                    "position": {"x": i, "y": 0},
                    "facing": "east",
                    "hp": 150,
                    "max_hp": 150,
                    "action_cooldown": 0,
                    "can_act": True,
                },
                "score": i,
                "scoreboard": {"cat-1": 20, "cat-2": 10, "cat-3": i},
                "visible_entities": [{"id": "cat-1", "type": "cat", "position": {"x": i + 2, "y": 0}}],
                "legal_actions": ["stay", "north", "south", "east", "west", "lunge", "attack"],
            },
        })

    capture = {}
    coach_prompt = build_coach_eval_prompt(records, agent_id="cat-3", capture=capture)

    # 1. Last 10 history & reasoning (ticks 6..15 in last 10 block, not tick 1..5)
    hist_sec = capture["sections"]["recent_history"]["text"]
    assert "Tick #6:" in hist_sec
    assert "Tick #15:" in hist_sec
    assert "Reasoning on tick 15" in hist_sec
    assert "Tick #5:" not in hist_sec

    # 2. Last 10 observation payloads (ticks 6..15)
    obs_sec = capture["sections"]["recent_observations"]["text"]
    assert "[Tick #6 Observation Payload]:" in obs_sec
    assert "[Tick #15 Observation Payload]:" in obs_sec
    assert "[Tick #5 Observation Payload]:" not in obs_sec

    # 3. Entirety of the latest payload facing the Actor (contains 25-tick window -> ticks 1..14 + current obs tick 15)
    actor_sec = capture["sections"]["latest_actor_payload"]["text"]
    assert "--- RECENT TACTICAL HISTORY ---" in actor_sec
    assert "- Tick 1:" in actor_sec
    assert "- Tick 14:" in actor_sec
    assert "--- CURRENT OBSERVATION (Tick 15, Round 1) ---" in actor_sec
    assert coach_prompt == capture["full_prompt"]


