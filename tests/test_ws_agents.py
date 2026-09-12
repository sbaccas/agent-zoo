import threading
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.api.admin import get_or_create_default_sim
from app.game.ws_manager import ws_agent_manager


def test_ws_agent_invalid_handshake():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/agents") as ws:
            ws.send_json({"type": "invalid_type"})
            data = ws.receive_json()
            assert data.get("type") == "error"
            assert "Expected 'register'" in data.get("message", "")


def test_ws_agent_registration_and_disconnect():
    sim = get_or_create_default_sim()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/agents") as ws:
            # Send register
            ws.send_json({
                "type": "register",
                "role": "cat",
                "agent_name": "pytest-cat",
                "requested_id": "cat-1",
            })
            data = ws.receive_json()
            assert data.get("type") == "registered"
            assert data.get("status") == "ok"
            assert data.get("agent_id") == "cat-1"
            assert ws_agent_manager.has_agent("cat-1")
            assert sim.entity_manager.get_agent_mode("cat-1") == "ws"

        # After exiting context (disconnect), mode should revert
        assert not ws_agent_manager.has_agent("cat-1")
        assert sim.entity_manager.get_agent_mode("cat-1") == "auto"


def test_ws_agent_action_dispatch():
    sim = get_or_create_default_sim()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/agents") as ws:
            ws.send_json({
                "type": "register",
                "role": "cat",
                "agent_name": "action-cat",
                "requested_id": "cat-1",
            })
            reg = ws.receive_json()
            assert reg.get("type") == "registered"

            # Trigger simulation step via HTTP POST in a thread
            step_result = []

            def run_step():
                res = client.post(f"/api/simulations/{sim.simulation_id}/step")
                step_result.append(res)

            t = threading.Thread(target=run_step)
            t.start()

            # Read the observation dispatched by the scheduler
            obs_msg = ws.receive_json()
            assert obs_msg.get("type") == "observation"
            assert "payload" in obs_msg

            # Respond with action
            ws.send_json({
                "type": "action",
                "tick": obs_msg.get("tick", 1),
                "action": "stay",
                "thought": "Standing still for test",
            })

            t.join(timeout=3.0)
            assert len(step_result) == 1
            assert step_result[0].status_code == 200
            assert sim.scheduler.tick >= 1


def test_ws_agent_decoupled_deliberation_and_timeout():
    """Verifies that decoupled action buffer and tick-budget timeout (stay) work seamlessly."""
    sim = get_or_create_default_sim()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/agents") as ws:
            ws.send_json({
                "type": "register",
                "role": "cat",
                "agent_name": "decoupled-cat",
                "requested_id": "cat-1",
            })
            reg = ws.receive_json()
            assert reg.get("type") == "registered"

            # 1. Test pre-buffering an action before tick starts
            ws_agent_manager.receive_action("cat-1", {
                "type": "action",
                "action": "south",
                "thought": "Pre-buffered fast reflex action",
                "strategy": "PURSUIT",
            })
            assert ws_agent_manager.peek_buffered_action("cat-1") is not None

            # Executing step should consume the buffered action immediately
            step_res = client.post(f"/api/simulations/{sim.simulation_id}/step")
            assert step_res.status_code == 200
            assert ws_agent_manager.peek_buffered_action("cat-1") is None

            # Drain observation from the previous step
            obs_msg = ws.receive_json()
            assert obs_msg.get("type") == "observation"

            # 2. Test tick timeout when agent exceeds budget (defaults to stay without crashing)
            # We don't send any action back. Step should complete with timeout fallback to stay
            step_res2 = client.post(f"/api/simulations/{sim.simulation_id}/step")
            assert step_res2.status_code == 200
            assert sim.scheduler.tick >= 2
