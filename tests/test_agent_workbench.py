import pytest
from starlette.testclient import TestClient
from actors.cat.api import app, state, Observation
from actors.cat import store


@pytest.fixture(autouse=True)
def reset_agent_state():
    state.history.clear()
    state.latest_observation = None
    state.latest_response = None
    state.latest_autonomous = None
    state.pending_override = None
    state.override_mode = "once"
    state.active_tick = 0
    state.total_ticks_processed = 0
    state.history_window_size = 25
    # Reflexion loop: the transcript is on disk, so it must be wiped between tests
    # or records leak across cases.
    store.reset_transcript()
    store.reset_coaching_notes()
    state.eval_interval = 100
    state.eval_enabled = True
    state.last_eval_dispatch_tick = 0
    state.last_eval_result = None
    state.eval_pending = False
    state.is_evaluating = False
    state.agent_id = "cat-1"
    from actors.cat.api import ws_client
    ws_client.requested_cat_id = "cat-1"
    ws_client.assigned_id = None


def test_workbench_html_served():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Cat Agent Workbench" in resp.text
    assert "/static/workbench.js" in resp.text


def test_workbench_state_endpoint():
    client = TestClient(app)
    resp = client.get("/api/agent/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["active_tick"] == 0
    assert data["history"] == []


def test_workbench_override_and_act():
    client = TestClient(app)

    # 1. Set override to 'west'
    override_resp = client.post("/api/agent/override", json={"action": "west", "mode": "once"})
    assert override_resp.status_code == 200
    assert override_resp.json()["pending_override"] == "west"

    # 2. Send observation where cat would normally attack mouse
    cat_obs = {
        "tick": 4,
        "round": 1,
        "self": {"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "north", "hp": 100},
        "visible_entities": [{"id": "mouse-1", "type": "mouse", "position": {"x": 10, "y": 9}}],
        "legal_actions": ["north", "south", "east", "west", "stay", "attack"],
    }
    act_resp = client.post("/act", json=cat_obs)
    assert act_resp.status_code == 200
    act_data = act_resp.json()

    # Manual override 'west' should have taken precedence over autonomous 'attack'
    assert act_data["action"] == "west"
    assert "Manual override" in act_data["thought"]

    # Since mode was 'once', pending override should now be cleared
    state_resp = client.get("/api/agent/state")
    state_data = state_resp.json()
    assert state_data["pending_override"] is None
    assert len(state_data["history"]) == 1
    assert state_data["active_tick"] == 4
    assert state_data["total_ticks_processed"] == 1
    assert state_data["history"][0]["source"] == "manual_override"
    assert state_data["history"][0]["autonomous_decision"]["action"] == "attack"
    assert state_data["history"][0]["response"]["resulting_posture"]["position"] == {"x": 9, "y": 10}
    assert state_data["history"][0]["response"]["resulting_posture"]["facing"] == "west"

    # 3. Next tick (tick 5) receives post-move observation: cat at (9, 10) facing west
    cat_obs["tick"] = 5
    cat_obs["self"]["position"] = {"x": 9, "y": 10}
    cat_obs["self"]["facing"] = "west"
    cat_obs["visible_entities"] = [{"id": "mouse-1", "type": "mouse", "position": {"x": 8, "y": 10}}]
    act_resp2 = client.post("/act", json=cat_obs)
    assert act_resp2.status_code == 200
    assert act_resp2.json()["action"] == "attack"

    # 4. Clear history
    clear_resp = client.post("/api/agent/clear-history")
    assert clear_resp.status_code == 200
    assert client.get("/api/agent/state").json()["history"] == []


@pytest.mark.asyncio
async def test_workbench_live_sync():
    from unittest.mock import patch, AsyncMock
    from actors.cat.api import sync_live_observation

    # Mock response from Game Server (:8000)
    mock_obs_data = {
        "simulation_id": "test-sim-1",
        "tick": 2,
        "round": 0,
        "self": {
            "id": "cat-1",
            "type": "cat",
            "position": {"x": 50, "y": 50},
            "facing": "east",
            "hp": 100,
        },
        "visible_entities": [{"id": "mouse-9", "type": "mouse", "position": {"x": 51, "y": 50}}],
        "legal_actions": ["stay", "north", "south", "east", "west", "attack"],
    }

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: mock_obs_data

    # Set up history with Tick 2 completed
    state.history.append({"tick": 2, "action": "east"})

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        synced = await sync_live_observation("cat-1")
        assert synced is True
        # Upcoming tick must be 3 because tick 2 is in history
        assert state.active_tick == 3
        assert state.latest_observation["tick"] == 3
        assert state.latest_observation["self"]["position"] == {"x": 50, "y": 50}
        assert state.latest_observation["self"]["facing"] == "east"
        # Autonomous brain should propose attack on the mouse ahead
        assert state.latest_autonomous["action"] == "attack"


def test_workbench_lunge_override():
    client = TestClient(app)

    # 1. Set override to 'lunge'
    override_resp = client.post("/api/agent/override", json={"action": "lunge", "mode": "once"})
    assert override_resp.status_code == 200
    assert override_resp.json()["pending_override"] == "lunge"

    # 2. Cat at (10, 10) facing EAST
    cat_obs = {
        "tick": 1,
        "round": 0,
        "self": {"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "east", "hp": 100},
        "visible_entities": [{"id": "mouse-1", "type": "mouse", "position": {"x": 14, "y": 10}}],
        "legal_actions": ["north", "south", "east", "west", "stay", "attack", "lunge"],
    }
    act_resp = client.post("/act", json=cat_obs)
    assert act_resp.status_code == 200
    assert act_resp.json()["action"] == "lunge"

    # Verify posture is 2 tiles ahead in facing direction (12, 10)
    state_resp = client.get("/api/agent/state")
    data = state_resp.json()
    assert len(data["history"]) == 1
    assert data["history"][0]["response"]["resulting_posture"]["position"] == {"x": 12, "y": 10}
    assert data["history"][0]["response"]["resulting_posture"]["facing"] == "east"


def test_skills_manual_endpoint():
    client = TestClient(app)
    resp = client.get("/api/agent/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data
    assert "skills.md" in data["path"]
    assert "Cat and Mouse" in data["content"]
    assert "lunge" in data["content"]
    assert "attack" in data["content"]


def test_set_api_key_endpoint():
    import os
    client = TestClient(app)

    # Set key
    resp = client.post("/api/agent/set-key", json={"api_key": "test-gemini-key-12345"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["has_api_key"] is True
    assert os.environ.get("GEMINI_API_KEY") == "test-gemini-key-12345"

    # State reflects key
    st_resp = client.get("/api/agent/state")
    assert st_resp.json()["has_api_key"] is True

    # Clear key
    resp_clear = client.post("/api/agent/set-key", json={"api_key": ""})
    assert resp_clear.status_code == 200
    assert resp_clear.json()["status"] == "cleared"
    assert resp_clear.json()["has_api_key"] is False
    assert "GEMINI_API_KEY" not in os.environ


def test_adk_agent_configuration():
    from actors.cat.api import adk_agent, CatTacticalDecision
    assert adk_agent.name == "cat_tactician"
    assert adk_agent.model == "gemini-2.5-flash"
    assert adk_agent.output_schema == CatTacticalDecision
    assert adk_agent.output_key == "decision"
    # `instruction` is an ADK InstructionProvider, not a baked string -- see
    # test_skills_instruction_is_read_per_call for why.
    assert callable(adk_agent.instruction)
    assert "Cat and Mouse" in adk_agent.instruction(None)


def test_skills_instruction_is_read_per_call():
    """The manual must be re-read on every call, never captured at import.

    When it was captured at import, editing actor_skills.md or eval_skills.md had
    no effect on a running server: uvicorn's --reload only watches .py files. The
    skills endpoints read the file directly, so the workbench displayed the new
    rules while the agents kept obeying the old ones -- silently, with no error.
    """
    from actors.cat.actor import adk_agent, adk_interrogator_agent, SKILLS_PATH
    from actors.cat.subagents.eval import eval_agent, EVAL_SKILLS_PATH

    for agent, path in (
        (adk_agent, SKILLS_PATH),
        (adk_interrogator_agent, SKILLS_PATH),
        (eval_agent, EVAL_SKILLS_PATH),
    ):
        assert callable(agent.instruction), (
            f"{agent.name} froze its instruction at import; edits to "
            f"{path.name} will not take effect on a running server"
        )

        original = path.read_text(encoding="utf-8")
        sentinel = "ZZZ_SENTINEL_INSTRUCTION_MARKER_ZZZ"
        try:
            path.write_text(original + f"\n\n{sentinel}\n", encoding="utf-8")
            assert sentinel in agent.instruction(None), (
                f"{agent.name} did not pick up a live edit to {path.name}"
            )
        finally:
            path.write_text(original, encoding="utf-8")

        assert path.read_text(encoding="utf-8") == original
        assert sentinel not in agent.instruction(None)


def test_act_adk_decision_mocked():
    from unittest.mock import patch
    from actors.cat.api import CatTacticalDecision
    client = TestClient(app)

    mock_decision = CatTacticalDecision(
        action="lunge",
        thought="Mouse aligned 3 tiles ahead in corridor! Lunging forward!",
        target_mouse_id="mouse-1",
        strategy="LUNGE",
    )

    cat_obs = {
        "tick": 7,
        "round": 1,
        "self": {"id": "cat-1", "position": {"x": 5, "y": 5}, "facing": "east", "hp": 100},
        "visible_entities": [{"id": "mouse-1", "type": "mouse", "position": {"x": 8, "y": 5}}],
        "legal_actions": ["stay", "north", "south", "east", "west", "attack", "lunge"],
    }

    with patch("actors.cat.api.query_adk_gemini", return_value=mock_decision):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
            resp = client.post("/act", json=cat_obs)
            assert resp.status_code == 200
            data = resp.json()
            assert data["action"] == "lunge"
            assert "Lunging forward" in data["thought"]

            # Verify history recorded adk_gemini as source
            st = client.get("/api/agent/state").json()
            assert len(st["history"]) == 1
            last_entry = st["history"][0]
            assert last_entry["source"] == "adk_gemini"
            assert last_entry["response"]["action"] == "lunge"
            assert last_entry["response"]["resulting_posture"]["position"] == {"x": 7, "y": 5}
            assert last_entry["autonomous_decision"]["meta"]["engine"] == "adk_gemini"
            assert last_entry["autonomous_decision"]["meta"]["phase"] == "LUNGE"


def test_workbench_server_connection_endpoints():
    client = TestClient(app)
    status_resp = client.get("/api/agent/server/status")
    assert status_resp.status_code == 200
    st = status_resp.json()
    assert "server_url" in st
    assert "connected" in st

    # Disconnect
    disc_resp = client.post("/api/agent/server/disconnect")
    assert disc_resp.status_code == 200
    assert disc_resp.json()["connected"] is False

    # Connect with custom URL and cat_id
    conn_resp = client.post("/api/agent/server/connect", json={
        "server_url": "ws://127.0.0.1:8000/ws/agents",
        "cat_id": "cat-2"
    })
    assert conn_resp.status_code == 200
    conn_data = conn_resp.json()
    assert "ws://127.0.0.1:8000/ws/agents" in conn_data["server_url"]
    assert conn_data["requested_cat_id"] == "cat-2"

    # Test /api/agent/server/cats
    cats_resp = client.get("/api/agent/server/cats")
    assert cats_resp.status_code == 200
    assert "cats" in cats_resp.json()

    # Clean up
    client.post("/api/agent/server/disconnect")


def test_reflex_decision_and_target_lock():
    from actors.cat.heuristic import compute_reflex_decision

    # 1. Test immediate strike zone attack reflex
    obs_strike = Observation(
        tick=1,
        round=0,
        self={"id": "cat-1", "position": {"x": 5, "y": 5}, "facing": "north"},
        visible_entities=[{"id": "mouse-1", "type": "mouse", "position": {"x": 5, "y": 4}}],
        legal_actions=["north", "south", "east", "west", "stay", "attack", "lunge"],
    )
    act, thought, meta = compute_reflex_decision(obs_strike)
    assert act == "attack"
    assert "strike zone" in thought.lower()
    assert meta["target_id"] == "mouse-1"

    # 2. Test target-lock pursuit (RuneScape style follow)
    obs_pursuit = Observation(
        tick=2,
        round=0,
        self={"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "north"},
        visible_entities=[
            {"id": "mouse-1", "type": "mouse", "position": {"x": 10, "y": 2}}, # farther away
            {"id": "mouse-2", "type": "mouse", "position": {"x": 15, "y": 10}}, # locked target to the east
        ],
        legal_actions=["north", "south", "east", "west", "stay"],
    )
    # Even though mouse-1 is north, target lock is on mouse-2 (east)
    act2, thought2, meta2 = compute_reflex_decision(obs_pursuit, locked_target_id="mouse-2", strategy="PURSUIT")
    assert act2 == "east"
    assert "mouse-2" in thought2
    assert meta2["target_id"] == "mouse-2"


@pytest.mark.asyncio
async def test_decoupled_act_async_execution():
    from actors.cat.api import act_async, state
    from unittest.mock import patch, AsyncMock
    from actors.models import CatTacticalDecision

    obs = Observation(
        tick=1,
        round=0,
        self={"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "north"},
        visible_entities=[{"id": "mouse-1", "type": "mouse", "position": {"x": 12, "y": 10}}],
        legal_actions=["north", "south", "east", "west", "stay", "attack"],
    )

    # In decoupled mode with credentials, it should respond reflexively in < 5ms without blocking
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch("actors.cat.api.model_query_adk_gemini", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                CatTacticalDecision(action="east", thought="Flanking mouse-1", strategy="STALKING", target_mouse_id="mouse-1"),
                "adk_gemini",
                None,
            )
            # Decoupled call
            resp = await act_async(obs, decoupled=True)
            assert resp.action in ["east", "attack", "north", "south", "stay"]

            # Wait briefly for the background task to finish and update active directive
            if state.deliberation_task:
                await state.deliberation_task
            assert state.active_directive is not None
            assert state.active_directive.get("target_mouse_id") == "mouse-1"


def test_dynamic_attack_range_in_heuristic():
    from actors.cat.heuristic import compute_heuristic_decision, compute_reflex_decision

    # Mouse is 5 tiles ahead (distance 5)
    # Default range (2) would NOT attack
    obs_default = Observation(
        tick=1,
        round=0,
        self={"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "north"},
        visible_entities=[{"id": "mouse-1", "type": "mouse", "position": {"x": 10, "y": 5}}],
        legal_actions=["north", "south", "east", "west", "stay", "attack"],
        capabilities={"attack_range": 2, "vision_radius": 6},
    )
    act_def, _, _ = compute_heuristic_decision(obs_default)
    assert act_def != "attack"

    # With dynamic attack_range=6, it SHOULD slash attack!
    obs_boosted = Observation(
        tick=1,
        round=0,
        self={"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "north"},
        visible_entities=[{"id": "mouse-1", "type": "mouse", "position": {"x": 10, "y": 5}}],
        legal_actions=["north", "south", "east", "west", "stay", "attack"],
        capabilities={"attack_range": 6, "vision_radius": 6},
    )
    act_boosted, thought_boosted, meta_boosted = compute_heuristic_decision(obs_boosted)
    assert act_boosted == "attack"
    assert "range 6" in thought_boosted
    assert meta_boosted["phase"] == "ATTACK"

    # Also test reflex trigger with dynamic range
    act_reflex, thought_reflex, meta_reflex = compute_reflex_decision(obs_boosted)
    assert act_reflex == "attack"
    assert "range 6" in thought_reflex


def test_history_window_endpoint():
    client = TestClient(app)

    # Initial state should have history_window_size == 25
    state_resp = client.get("/api/agent/state")
    assert state_resp.status_code == 200
    assert state_resp.json()["history_window_size"] == 25

    # Update to 10
    update_resp = client.post("/api/agent/history-window", json={"history_window_size": 10})
    assert update_resp.status_code == 200
    assert update_resp.json()["history_window_size"] == 10
    assert state.history_window_size == 10

    # Verify reflected in state endpoint
    state_resp2 = client.get("/api/agent/state")
    assert state_resp2.json()["history_window_size"] == 10

    # The slider spans 1-50, so both ends must be accepted unclamped.
    assert client.post("/api/agent/history-window", json={"history_window_size": 1}).json()["history_window_size"] == 1
    assert client.post("/api/agent/history-window", json={"history_window_size": 50}).json()["history_window_size"] == 50

    # Reset back to the default
    client.post("/api/agent/history-window", json={"history_window_size": 25})
    assert state.history_window_size == 25


def test_interrogate_agent_endpoint():
    client = TestClient(app)

    # Clear chat
    client.post("/api/agent/clear-chat")
    assert len(state.chat_history) == 0

    # Post interrogation prompt
    prompt_resp = client.post(
        "/api/agent/prompt",
        json={
            "prompt": "WHAT ARE YOU THINKING???? why aren't you exploring more of the board?",
            "steer_directive": True,
        },
    )
    assert prompt_resp.status_code == 200
    data = prompt_resp.json()
    assert "reply" in data
    assert len(data["reply"]) > 0
    assert data["adopted_directive"] == "WHAT ARE YOU THINKING???? why aren't you exploring more of the board?"

    # Check chat history
    chat_resp = client.get("/api/agent/chat-history")
    assert chat_resp.status_code == 200
    chat_list = chat_resp.json()
    assert len(chat_list) == 2
    assert chat_list[0]["sender"] == "commander"
    assert chat_list[1]["sender"] == "cat"

    # Verify chat clear
    clear_resp = client.post("/api/agent/clear-chat")
    assert clear_resp.status_code == 200
    assert len(state.chat_history) == 0



# ---------------------------------------------------------------------------
# Reflexion loop: transcript -> evaluator -> coaching notes -> actor
# ---------------------------------------------------------------------------


def _obs(tick, x=3, y=4, facing="east"):
    return {
        "tick": tick,
        "round": 1,
        "self": {"id": "cat-1", "position": {"x": x, "y": y}, "facing": facing},
        "visible_entities": [],
        "legal_actions": ["stay", "north", "south", "east", "west"],
        "capabilities": {"attack_range": 2, "vision_radius": 4},
    }


def test_actor_and_evaluator_have_separate_skills_files():
    """The evaluator must not share the actor's skills manual."""
    from actors.cat import actor
    from actors.cat.subagents import eval as evaluator

    assert actor.SKILLS_PATH.name == "actor_skills.md"
    assert evaluator.EVAL_SKILLS_PATH.name == "eval_skills.md"
    assert actor.SKILLS_PATH != evaluator.EVAL_SKILLS_PATH
    assert actor.SKILLS_PATH.exists()
    assert evaluator.EVAL_SKILLS_PATH.exists()


def test_transcript_persists_every_tick_beyond_memory_cap():
    """The disk transcript must outlive the in-memory history cap.

    This is the whole point of moving it to disk: a 100-tick evaluation cannot
    run off a 50-entry rolling buffer.
    """
    client = TestClient(app)
    state.max_history = 5

    try:
        for tick in range(1, 21):
            assert client.post("/act", json=_obs(tick)).status_code == 200

        assert len(state.history) == 5, "in-memory history should stay capped"
        assert store.transcript_tick_count() == 20, "disk transcript must keep every tick"
    finally:
        state.max_history = 50


def test_transcript_records_are_compact():
    """Each record carries the trajectory fields the evaluator reasons over."""
    client = TestClient(app)
    client.post("/act", json=_obs(1, x=7, y=9, facing="north"))

    records = store.read_transcript_records()
    assert len(records) == 1
    rec = records[0]
    assert set(rec) == {
        "tick",
        "x",
        "y",
        "facing",
        "action",
        "thought",
        "source",
        # The lock dimension. `source` says which engine decided; these say
        # whether the engine let it happen. The evaluator needs both, and
        # conflating them made rejected commands read as deliberate repetition.
        "tick_type",
        "executed",
        "action_cooldown",
        "locked_action",
        "visible",
        "score",
        # The live tunables the actor acted on. The evaluator has no
        # observation of its own, so without these it cannot know the cat's
        # actual reach and has historically guessed it wrong.
        "capabilities",
        "observation",
    }
    assert rec["tick"] == 1
    assert rec["action"] in ("stay", "north", "south", "east", "west", "lunge", "attack")
    # The evaluator infers hit/miss from the score delta across records, so the
    # counter must survive compaction rather than being dropped as noise.
    assert isinstance(rec["score"], int)
    # An unlocked observation is a real play tick.
    assert rec["tick_type"] == "play"
    assert rec["executed"] is True


def test_locked_tick_is_recorded_as_unexecuted_and_does_not_move_the_cat():
    """A locked tick must not be recorded as a move.

    The transcript used to store the posture the chosen action *would* have
    produced, even while the engine was rejecting that action. Over a long game
    that inflated the recorded path to roughly twice the distance the cat could
    physically travel, and the evaluator reads this file.
    """
    client = TestClient(app)
    obs = _obs(1, x=7, y=9, facing="north")
    obs["self"]["action_cooldown"] = 2
    obs["self"]["locked_action"] = "north"

    assert client.post("/act", json=obs).status_code == 200

    rec = store.read_transcript_records()[-1]
    assert rec["tick_type"] == "cooldown"
    assert rec["executed"] is False
    assert rec["action_cooldown"] == 2
    assert rec["locked_action"] == "north"
    # Position must be exactly where the cat already was, not where the
    # discarded action pointed.
    assert (rec["x"], rec["y"]) == (7, 9)
    assert rec["facing"] == "north"


def test_final_locked_tick_is_not_mistaken_for_a_play_tick():
    """The last tick of a lock arrives with the counter already at zero.

    The scheduler decrements `action_cooldown` *before* it builds the payload and
    skips the turn, so the final locked tick is delivered as cooldown 0 while
    still being unplayable. Inferring lock state from the counter alone counted
    that tick as a real move -- two "play" ticks per three-tick cycle for a cat
    that can only act once. `can_act` is the authoritative signal.
    """
    client = TestClient(app)
    obs = _obs(1, x=7, y=9, facing="north")
    obs["self"]["action_cooldown"] = 0
    obs["self"]["locked_action"] = None
    obs["self"]["can_act"] = False

    assert client.post("/act", json=obs).status_code == 200

    rec = store.read_transcript_records()[-1]
    assert rec["tick_type"] == "cooldown", "can_act=False must win over a zeroed counter"
    assert rec["executed"] is False
    assert (rec["x"], rec["y"]) == (7, 9)


def test_evaluator_interval_is_adjustable():
    client = TestClient(app)
    resp = client.post("/api/agent/evaluator/interval", json={"interval": 25})
    assert resp.status_code == 200
    assert resp.json()["interval"] == 25
    assert state.eval_interval == 25

    # Out-of-range values are clamped rather than rejected outright.
    client.post("/api/agent/evaluator/interval", json={"interval": 1})
    assert state.eval_interval == 1


def test_evaluator_can_be_disabled():
    client = TestClient(app)
    client.post("/api/agent/evaluator/interval", json={"interval": 2, "enabled": False})
    assert state.eval_enabled is False

    for tick in range(1, 8):
        client.post("/act", json=_obs(tick))

    assert state.eval_pending is False, "disabled evaluator must not queue work"


def test_evaluation_queued_on_interval_boundary():
    """Crossing the interval raises a request for the background worker."""
    client = TestClient(app)
    client.post("/api/agent/evaluator/interval", json={"interval": 5})

    for tick in range(1, 5):
        client.post("/act", json=_obs(tick))
    assert state.eval_pending is False, "must not fire before the boundary"

    client.post("/act", json=_obs(5))
    assert state.eval_pending is True, "must fire on the boundary"
    assert state.last_eval_dispatch_tick == 5


def test_forced_evaluation_publishes_notes_for_actor():
    """The full hand-off: transcript in, coaching notes out, actor reads them."""
    from actors.cat import actor

    client = TestClient(app)
    for tick in range(1, 7):
        client.post("/act", json=_obs(tick, x=3 + tick, y=4))

    assert store.read_coaching_notes() == "", "no notes before evaluation"

    resp = client.post("/api/agent/evaluator/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    notes = store.read_coaching_notes()
    assert notes, "evaluator published no notes"
    # The actor must read the same notes the evaluator wrote.
    assert actor.store.read_coaching_notes() == notes

    result = state.last_eval_result
    assert result["ticks_evaluated"] == 6
    assert result["source"] in ("adk_gemini", "offline_fallback")


def test_evaluator_status_endpoint():
    client = TestClient(app)
    client.post("/api/agent/evaluator/interval", json={"interval": 40})
    for tick in range(1, 4):
        client.post("/act", json=_obs(tick))

    data = client.get("/api/agent/evaluator").json()
    assert data["enabled"] is True
    assert data["interval"] == 40
    assert data["transcript_ticks"] == 3
    assert data["next_dispatch_tick"] == 40


def test_clear_history_wipes_transcript_and_notes():
    """A reset must not let a new game inherit the old game's standing orders."""
    client = TestClient(app)
    for tick in range(1, 5):
        client.post("/act", json=_obs(tick))
    client.post("/api/agent/evaluator/run")

    assert store.transcript_tick_count() == 4
    assert store.read_coaching_notes() != ""

    client.post("/api/agent/clear-history")

    assert store.transcript_tick_count() == 0
    assert store.read_coaching_notes() == ""
    # The eval clock rebases to the current tick, NOT to 0. Zeroing it meant
    # `tick - 0 >= interval` was already true, so the next tick fired an
    # evaluation against a transcript holding a single record.
    assert state.last_eval_dispatch_tick == state.active_tick
    assert state.last_eval_dispatch_tick != 0
    assert state.last_eval_result is None


def test_state_endpoint_exposes_evaluator():
    client = TestClient(app)
    data = client.get("/api/agent/state").json()
    assert "evaluator" in data
    assert data["evaluator"]["interval"] == 100
    assert data["evaluator"]["enabled"] is True


# ---------------------------------------------------------------------------
# Outcome feedback: the score must survive the whole path from the game server
# to the text the model actually reads.
# ---------------------------------------------------------------------------


def _score_obs(tick, score=0, scoreboard=None, action_hint=None):
    payload = _obs(tick)
    payload["score"] = score
    payload["scoreboard"] = scoreboard if scoreboard is not None else {"cat-1": score}
    if action_hint:
        payload["legal_actions"] = action_hint
    return payload


def test_observation_model_accepts_score_and_scoreboard():
    """The WebSocket path splats the raw payload straight into Observation."""
    obs = Observation(**_score_obs(1, score=3, scoreboard={"cat-1": 3, "cat-2": 1}))
    assert obs.score == 3
    assert obs.scoreboard == {"cat-1": 3, "cat-2": 1}


def test_observation_score_defaults_to_zero_when_absent():
    """Older payloads without a score must not blow up the agent."""
    obs = Observation(**_obs(1))
    assert obs.score == 0
    assert obs.scoreboard == {}


def test_score_reaches_history_and_transcript():
    client = TestClient(app)
    client.post("/act", json=_score_obs(1, score=0))
    client.post("/act", json=_score_obs(2, score=1))

    assert state.history[-1]["observation"]["score"] == 1
    records = store.read_transcript_records()
    assert [r["score"] for r in records] == [0, 1]


def test_http_sync_forwards_score_from_game_server():
    """The polled path whitelists fields, so it can silently drop new ones.

    The WebSocket path splats the payload wholesale and would keep working,
    which is exactly what makes a regression here easy to miss.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    import actors.cat.api as cat_api

    server_payload = {
        "simulation_id": "sim-test",
        "tick": 7,
        "round": 1,
        "self": {"id": "cat-1", "position": {"x": 2, "y": 2}, "facing": "east"},
        "visible_entities": [],
        "legal_actions": ["stay", "north", "south", "east", "west"],
        "capabilities": {"attack_range": 2, "vision_radius": 4},
        "score": 5,
        "scoreboard": {"cat-1": 5, "cat-2": 2},
    }

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: server_payload

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        ok = asyncio.run(cat_api.sync_live_observation("cat-1"))

    assert ok is True, "sync failed outright; the assertions below would be vacuous"
    assert state.latest_observation["score"] == 5
    assert state.latest_observation["scoreboard"] == {"cat-1": 5, "cat-2": 2}


def _capture_prompt(obs_payload, history, window=25):
    """Run the real prompt builder, intercepting the message bound for Gemini."""
    import asyncio
    import os
    from unittest.mock import AsyncMock, patch

    from actors.cat import actor

    captured = {}

    async def fake_run_async(*, user_id, session_id, new_message):
        captured["prompt"] = new_message.parts[0].text
        return
        yield  # noqa: unreachable -- makes this an async generator

    env = dict(os.environ)
    env["ENABLE_LIVE_LLM_TESTS"] = "1"
    with patch.dict(os.environ, env, clear=True), \
            patch.object(actor, "has_gemini_credentials", lambda: True), \
            patch.object(actor.session_service, "delete_session", new=AsyncMock()), \
            patch.object(actor.session_service, "create_session", new=AsyncMock()), \
            patch.object(actor.adk_runner, "run_async", new=fake_run_async):
        asyncio.run(
            actor.query_adk_gemini(
                Observation(**obs_payload),
                history=history,
                agent_id="cat-1",
                history_window_size=window,
            )
        )
    return captured.get("prompt", "")


def test_score_is_rendered_into_every_history_line():
    """Without a per-tick score the model cannot compute a delta.

    The history renderer emits a hand-picked subset of each observation, so a
    field present in the payload is not necessarily present in the prompt. The
    current score alone is useless -- there is nothing to compare it against --
    so the value must appear on each historical line.
    """
    history = [
        {"tick": 10, "observation": _score_obs(10, score=0),
         "response": {"action": "attack", "thought": "swing"}},
        {"tick": 11, "observation": _score_obs(11, score=1),
         "response": {"action": "east", "thought": "reposition"}},
    ]
    prompt = _capture_prompt(_score_obs(12, score=1), history)

    assert prompt, "prompt was never built; the interception point has moved"
    assert "Tick 10" in prompt and "Tick 11" in prompt
    # The delta 0 -> 1 straddling the attack is the entire signal.
    assert "SCORE: 0" in prompt
    assert "SCORE: 1" in prompt


def test_current_score_and_rivals_are_stated_in_the_prompt():
    prompt = _capture_prompt(
        _score_obs(12, score=4, scoreboard={"cat-1": 4, "cat-2": 9}), history=[]
    )

    assert "Your Score: 4" in prompt
    assert "cat-2: 9" in prompt
    assert "cat-1: 4" not in prompt.split("Rival Cats:")[1].split("\n")[0], (
        "the cat's own score was listed among its rivals"
    )


def test_prompt_does_not_interpret_the_outcome_for_the_model():
    """Tripwire: the prompt should hand over raw state, not a verdict.

    The design deliberately withholds any hit/miss label so the model infers the
    outcome by diffing the counter itself. This checks for the specific phrasings
    from the rejected 'last_action_result' approach.

    It is a tripwire, not a proof. Narration cannot be enumerated, so a novel
    phrasing will slip past this test. Treat a pass as "the known regression did
    not recur", not as "the prompt is free of interpretation".
    """
    history = [
        {"tick": 10, "observation": _score_obs(10, score=0),
         "response": {"action": "attack", "thought": "swing"}},
    ]
    prompt = _capture_prompt(_score_obs(11, score=0), history).lower()

    assert prompt, "prompt was never built; these assertions would pass vacuously"
    verdicts = (
        "you missed", "you hit", "attack missed", "attack hit",
        "last action result", "struck nothing", "hit nothing",
        "did not connect", "was a miss", "was a hit", "connected with",
        "your attack failed", "outcome:", "result of your last",
    )
    for verdict in verdicts:
        assert verdict not in prompt, f"prompt is interpreting the outcome: {verdict!r}"
