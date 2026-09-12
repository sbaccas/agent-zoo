import pytest
from starlette.testclient import TestClient
from actors.cat_agent import app, state, Observation


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
    from actors.cat_agent import sync_live_observation

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
    from actors.cat_agent import adk_agent, CatTacticalDecision
    assert adk_agent.name == "cat_tactician"
    assert adk_agent.model == "gemini-2.5-flash"
    assert adk_agent.output_schema == CatTacticalDecision
    assert adk_agent.output_key == "decision"
    assert "Cat and Mouse" in adk_agent.instruction


def test_act_adk_decision_mocked():
    from unittest.mock import patch
    from actors.cat_agent import CatTacticalDecision
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

    with patch("actors.cat_agent.query_adk_gemini", return_value=mock_decision):
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
    from actors.cat_heuristic import compute_reflex_decision

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
    from actors.cat_agent import act_async, state
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
        with patch("actors.cat_agent.model_query_adk_gemini", new_callable=AsyncMock) as mock_llm:
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



