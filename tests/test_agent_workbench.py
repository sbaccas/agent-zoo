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
