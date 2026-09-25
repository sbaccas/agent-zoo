import pytest
from app.game.simulation import Simulation
from app.models.simulation import SimulationConfig
from app.models.entity import EntityType, Position, Direction
from actors.models import Observation, CatTacticalDecision, ActionResponse
from actors.cat.actor import build_decision_prompt


@pytest.fixture
def empty_sim():
    config = SimulationConfig(
        width=20,
        height=20,
        populations={"cats": 0, "mice": 0},
        enable_map_features=False,
    )
    sim = Simulation(config=config)
    sim.entity_manager.entities.clear()
    sim.action_handler.hazard_zones.clear()
    sim.scheduler.scores.clear()
    sim.scheduler.transmissions.clear()
    return sim


def test_transmission_recorded_and_delivered_in_observations(empty_sim):
    sim = empty_sim
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=5, y=5), facing=Direction.NORTH)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.SOUTH)

    # Cat 2 broadcasts a tactical chirp
    msg_text = "Spotted mice near east corridor!"
    transmission_record = {
        "tick": sim.tick,
        "round": sim.round,
        "sender_id": cat2.id,
        "message": msg_text,
        "position": {"x": cat2.position.x, "y": cat2.position.y},
    }
    sim.scheduler.transmissions.append(transmission_record)

    # Observation built for Cat 1 should include Cat 2's transmission
    obs1 = sim.scheduler._build_observation_payload(cat1)
    assert "incoming_transmissions" in obs1
    assert len(obs1["incoming_transmissions"]) == 1
    assert obs1["incoming_transmissions"][0]["sender_id"] == cat2.id
    assert obs1["incoming_transmissions"][0]["message"] == msg_text

    # Observation built for Cat 2 should NOT include Cat 2's own transmission
    obs2 = sim.scheduler._build_observation_payload(cat2)
    assert "incoming_transmissions" in obs2
    assert len(obs2["incoming_transmissions"]) == 0


def test_build_decision_prompt_formats_transmissions():
    obs = Observation(
        tick=42,
        round=1,
        self={"id": "cat-1", "position": {"x": 5, "y": 5}, "facing": "north", "hp": 100},
        legal_actions=["stay", "north", "south", "east", "west"],
        incoming_transmissions=[
            {
                "tick": 41,
                "round": 1,
                "sender_id": "cat-2",
                "message": "Flanking mouse-3 from south!",
                "position": {"x": 8, "y": 9},
            }
        ],
    )
    capture = {}
    prompt = build_decision_prompt(obs, capture=capture, agent_id="cat-1")

    # Prompt text checks
    assert "--- INTER-CAT RADIO (Incoming Transmissions) ---" in prompt
    assert "Flanking mouse-3 from south!" in prompt
    assert "cat-2" in prompt

    # Workbench prompt inspector section checks
    assert "inter_cat_radio" in capture["sections"]
    section = capture["sections"]["inter_cat_radio"]
    assert section["delivered"] is True
    assert "cat-2" in section["text"]


@pytest.mark.asyncio
async def test_transmissions_snapshot_rewind_and_reset(empty_sim):
    sim = empty_sim
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=5, y=5), facing=Direction.NORTH)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.SOUTH)

    # Step 0 snapshot saved
    sim._save_snapshot()

    # Tick 1: cat1 broadcasts
    sim.scheduler.tick = 1
    sim.scheduler.transmissions.append({
        "tick": 1,
        "round": 0,
        "sender_id": cat1.id,
        "message": "Moving north",
        "position": {"x": 5, "y": 5},
    })
    sim._save_snapshot()
    assert len(sim.scheduler.transmissions) == 1

    # Tick 2: cat2 broadcasts
    sim.scheduler.tick = 2
    sim.scheduler.transmissions.append({
        "tick": 2,
        "round": 0,
        "sender_id": cat2.id,
        "message": "Spotted prey at (15, 15)",
        "position": {"x": 10, "y": 10},
    })
    assert len(sim.scheduler.transmissions) == 2

    # Step back should rewind to snapshot at tick 1
    await sim.step_back()
    assert sim.scheduler.tick == 1
    assert len(sim.scheduler.transmissions) == 1
    assert sim.scheduler.transmissions[0]["sender_id"] == cat1.id

    # Reset clears all transmissions
    await sim.reset()
    assert len(sim.scheduler.transmissions) == 0


def test_get_comms_log_on_simulation(empty_sim):
    sim = empty_sim
    for i in range(10):
        sim.scheduler.transmissions.append({
            "tick": i,
            "round": 0,
            "sender_id": "cat-1",
            "message": f"Chirp {i}",
            "position": {"x": i, "y": i},
        })

    log = sim.get_comms_log(limit=5)
    assert len(log) == 5
    assert log[-1]["message"] == "Chirp 9"
    assert log[0]["message"] == "Chirp 5"


def test_admin_comms_api_endpoint(empty_sim):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.admin import simulations, default_sim_id

    # Register our sim as active
    sim = empty_sim
    sim.scheduler.transmissions.append({
        "tick": 10,
        "round": 0,
        "sender_id": "cat-2",
        "message": "Moving north-east",
        "position": {"x": 3, "y": 4},
    })
    simulations[sim.simulation_id] = sim

    client = TestClient(app)
    resp = client.get(f"/api/simulations/{sim.simulation_id}/comms?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "transmissions" in data
    assert len(data["transmissions"]) == 1
    assert data["transmissions"][0]["sender_id"] == "cat-2"
    assert data["transmissions"][0]["message"] == "Moving north-east"


@pytest.mark.asyncio
async def test_mcp_comms_tool_and_endpoint(monkeypatch):
    from starlette.testclient import TestClient
    import app.mcp.server as mcp_mod

    # Mock _fetch_from_game_server to return simulated comms
    sample_transmissions = [
        {"tick": 5, "round": 0, "sender_id": "cat-1", "message": "Patrolling west", "position": {"x": 2, "y": 2}}
    ]

    async def mock_fetch(endpoint, params=None, method="GET", json_body=None):
        if "/comms" in endpoint:
            return True, {"transmissions": sample_transmissions}, None
        return False, None, "not found"

    monkeypatch.setattr(mcp_mod, "_fetch_from_game_server", mock_fetch)

    mcp_app = mcp_mod.create_mcp_app()
    client = TestClient(mcp_app)

    # Test REST endpoint
    resp = client.get("/api/mcp/comms")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert len(body.get("transmissions", [])) == 1
    assert body["transmissions"][0]["message"] == "Patrolling west"

    # Test MCP tool execution via /api/mcp/execute-tool
    exec_resp = client.post("/api/mcp/execute-tool", json={"tool": "get_comms_log", "arguments": {"limit": 10}})
    assert exec_resp.status_code == 200
    exec_body = exec_resp.json()
    assert exec_body.get("ok") is True
    res_payload = exec_body.get("result", {})
    assert len(res_payload.get("transmissions", [])) == 1


def test_agent_card_structure_and_well_known_endpoint():
    from fastapi.testclient import TestClient
    from actors.cat.api import app as cat_app
    from actors.a2a.models import AgentCard

    client = TestClient(cat_app)

    # Test /.well-known/agent.json
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = AgentCard.model_validate(resp.json())
    assert card.protocol_version == "a2a/v1"
    assert "messages" in card.endpoints
    assert card.capabilities.get("direct_messaging") is True
    assert card.capabilities.get("tools") is True
    assert any(s.id == "combat" for s in card.skills)

    # Test /a2a/agent-card alias
    resp_alias = client.get("/a2a/agent-card")
    assert resp_alias.status_code == 200
    assert resp_alias.json()["id"] == card.id


def test_private_direct_message_delivery_and_filtering(empty_sim):
    sim = empty_sim
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=2, y=2), facing=Direction.NORTH)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=5, y=5), facing=Direction.SOUTH)
    cat3 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=8, y=8), facing=Direction.WEST)

    # Cat-1 sends a private DM to Cat-2
    secret_dm = {
        "tick": 10,
        "round": 0,
        "sender_id": cat1.id,
        "recipient_id": cat2.id,
        "message": "Secret alliance proposal: let us corner mouse-7 together",
        "type": "DIRECT_MESSAGE",
        "protocol": "a2a/v1",
    }
    sim.scheduler.transmissions.append(secret_dm)

    # Cat-2 (recipient) SHOULD see the DM
    obs2 = sim.scheduler._build_observation_payload(cat2)
    assert len(obs2["incoming_transmissions"]) == 1
    assert obs2["incoming_transmissions"][0]["sender_id"] == cat1.id
    assert "Secret alliance" in obs2["incoming_transmissions"][0]["message"]

    # Cat-1 (sender) should NOT see its own message in incoming
    obs1 = sim.scheduler._build_observation_payload(cat1)
    assert len(obs1["incoming_transmissions"]) == 0

    # Cat-3 (third party rival) should NOT see the private DM!
    obs3 = sim.scheduler._build_observation_payload(cat3)
    assert len(obs3["incoming_transmissions"]) == 0


def test_a2a_tools_execution_and_trace(monkeypatch):
    from actors.a2a.tools import a2a_send_message, a2a_broadcast, a2a_get_agent_card, set_deliberation_context
    from actors.a2a.models import A2AResponse, AgentCard

    trace = set_deliberation_context(agent_id="cat-1", tick=15, round_num=1)

    # Mock send_a2a_message
    async def mock_send(sender_id, recipient_id, content, message_type="DIRECT_MESSAGE", **kwargs):
        return A2AResponse(status="delivered", message_id="msg_test_123", sender_id=sender_id, recipient_id=recipient_id)

    monkeypatch.setattr("actors.a2a.client.send_a2a_message", mock_send)

    # Invoke tool: a2a_send_message
    res = a2a_send_message("cat-2", "Let's trap mouse-4 at (10, 12)")
    assert "delivered to cat-2" in res
    assert len(trace) == 1
    assert trace[0]["tool"] == "a2a_send_message"
    assert trace[0]["recipient_id"] == "cat-2"

    # Invoke tool: a2a_broadcast
    res_b = a2a_broadcast("Arena broadcast: clear the north zone")
    assert "dispatched" in res_b
    assert len(trace) == 2
    assert trace[1]["tool"] == "a2a_broadcast"


@pytest.mark.asyncio
async def test_coach_to_coach_a2a_tools_and_diplomacy(monkeypatch, tmp_path):
    from actors.cat.subagents.eval import (
        a2a_send_coach_message,
        a2a_read_coach_messages,
        a2a_get_peer_coach_card,
        build_eval_prompt,
    )
    from actors.a2a.models import A2AResponse, AgentCard, AgentSkill
    from actors.a2a import store as a2a_store

    # Mock client.send_a2a_message
    sent_envelopes = []

    async def mock_send(sender_id, recipient_id, content, message_type="DIRECT_MESSAGE", **kwargs):
        sent_envelopes.append({
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "content": content,
            "message_type": message_type,
        })
        return A2AResponse(
            status="delivered",
            message_id="msg_coach_001",
            sender_id=sender_id,
            recipient_id=recipient_id,
        )

    monkeypatch.setattr("actors.a2a.client.send_a2a_message", mock_send)

    # 1. Coach of cat-1 dispatches strategic proposal to Coach of cat-2
    res = await a2a_send_coach_message(
        recipient_coach="coach(cat-2)",
        message="Let us split the arena: cat-1 patrols west, cat-2 patrols east.",
        message_type="PROPOSAL",
        cat_id="cat-1",
    )
    assert res["status"] == "delivered"
    assert res["sender"] == "coach(cat-1)"
    assert res["recipient"] == "coach(cat-2)"
    assert len(sent_envelopes) == 1
    assert sent_envelopes[0]["sender_id"] == "coach(cat-1)"
    assert sent_envelopes[0]["recipient_id"] == "coach(cat-2)"
    assert sent_envelopes[0]["message_type"] == "PROPOSAL"

    # 2. Test peer coach card retrieval
    async def mock_fetch_card(agent_id, **kwargs):
        return AgentCard(
            id=agent_id,
            name=f"Predator {agent_id.upper()}",
            description="Dual-consciousness predator",
            protocol_version="a2a/v1",
            metadata={
                "consciousness": {
                    "short_term": {"id": agent_id, "role": "Tactical Actor"},
                    "long_term": {"id": f"coach({agent_id})", "role": "Strategic Coach"},
                }
            },
        )

    monkeypatch.setattr("actors.a2a.client.fetch_agent_card", mock_fetch_card)
    card_res = await a2a_get_peer_coach_card(peer_id="cat-2", cat_id="cat-1")
    assert card_res["status"] == "ok"
    assert card_res["agent_card"]["metadata"]["consciousness"]["long_term"]["id"] == "coach(cat-2)"

    # 3. Simulate incoming message in cat-2's inbox from coach(cat-1)
    from actors.a2a.models import A2AMessage
    inbox_msg = A2AMessage(
        sender_id="coach(cat-1)",
        recipient_id="coach(cat-2)",
        content="Split territory proposal: x < 10 for cat-1, x >= 10 for cat-2",
        message_type="PROPOSAL",
    )
    a2a_store.append_inbox("cat-2", inbox_msg)

    # Cat-2 Coach reads inbox
    read_res = await a2a_read_coach_messages(cat_id="cat-2")
    assert read_res["available"] is True
    assert read_res["count"] >= 1
    assert any(m["sender"] == "coach(cat-1)" for m in read_res["messages"])

    # 4. Verify evaluation prompt incorporates peer coach comms
    eval_prompt = build_eval_prompt(records=[], previous="", agent_id="cat-2")
    assert "DEDICATED COACH FOR CAT-2 (LONG-TERM CONSCIOUSNESS)" in eval_prompt
    assert "--- A2A STRATEGIC DIPLOMACY (PEER COACH COMMS) ---" in eval_prompt
    assert "coach(cat-1)" in eval_prompt
    assert "Split territory proposal" in eval_prompt

    # Clean up test artifacts from inbox
    a2a_store.clear_a2a_history("cat-1")
    a2a_store.clear_a2a_history("cat-2")


def test_coach_dual_consciousness_agent_card():
    from actors.a2a.store import build_default_agent_card
    card1 = build_default_agent_card("cat-1", 8001)
    card2 = build_default_agent_card("coach(cat-2)", 8003)

    assert card1.id == "cat-1"
    assert card2.id == "cat-2"

    # Consciousness metadata
    meta1 = card1.metadata.get("consciousness", {})
    assert meta1["short_term"]["id"] == "cat-1"
    assert meta1["long_term"]["id"] == "coach(cat-1)"

    meta2 = card2.metadata.get("consciousness", {})
    assert meta2["short_term"]["id"] == "cat-2"
    assert meta2["long_term"]["id"] == "coach(cat-2)"

    # Skills declaration includes coach diplomacy
    assert any(s.id == "coach_diplomacy" for s in card1.skills)
    assert any(s.id == "coach_diplomacy" for s in card2.skills)
    assert card1.capabilities.get("coach_to_coach_diplomacy") is True

