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

    # 3. Next tick without override should execute autonomous 'attack'
    cat_obs["tick"] = 5
    act_resp2 = client.post("/act", json=cat_obs)
    assert act_resp2.status_code == 200
    assert act_resp2.json()["action"] == "attack"

    # 4. Clear history
    clear_resp = client.post("/api/agent/clear-history")
    assert clear_resp.status_code == 200
    assert client.get("/api/agent/state").json()["history"] == []
