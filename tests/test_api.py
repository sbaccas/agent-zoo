import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_get_simulation_state():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/simulations/active/state")
        assert res.status_code == 200
        data = res.json()
        assert "tick" in data
        assert "round" in data
        assert "width" in data
        assert "entities" in data


@pytest.mark.asyncio
async def test_simulation_step_api():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step once
        res = await client.post("/api/simulations/active/step")
        assert res.status_code == 200
        data = res.json()
        assert data["tick"] >= 1


@pytest.mark.asyncio
async def test_security_headers():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/")
        assert res.status_code == 200
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert "Content-Security-Policy" in res.headers


@pytest.mark.asyncio
async def test_step_back_api():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step forward
        res1 = await client.post("/api/simulations/active/step")
        assert res1.status_code == 200
        tick1 = res1.json()["tick"]

        # Step back
        res2 = await client.post("/api/simulations/active/step_back")
        assert res2.status_code == 200
        tick2 = res2.json()["tick"]
        assert tick2 == tick1 - 1


@pytest.mark.asyncio
async def test_agents_api_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. List agents
        res = await client.get("/api/simulations/active/agents")
        assert res.status_code == 200
        agents = res.json()
        assert len(agents) > 0
        first_agent = agents[0]
        agent_id = first_agent["id"]

        # 2. Register agent mode
        res_reg = await client.post(
            f"/api/simulations/active/agents/{agent_id}/register",
            json={"mode": "manual", "agent_url": None},
        )
        assert res_reg.status_code == 200
        assert res_reg.json()["mode"] == "manual"

        # 3. Get observation
        res_obs = await client.get(f"/api/simulations/active/agents/{agent_id}/observation")
        assert res_obs.status_code == 200
        obs_data = res_obs.json()
        assert "self" in obs_data
        assert "legal_actions" in obs_data
        assert obs_data["self"]["id"] == agent_id

        # 4. Submit manual action
        legal = obs_data["legal_actions"]
        action_choice = legal[0]
        res_act = await client.post(
            f"/api/simulations/active/agents/{agent_id}/actions",
            json={"action": action_choice},
        )
        assert res_act.status_code == 200
        act_data = res_act.json()
        assert act_data["accepted"] is True


@pytest.mark.asyncio
async def test_actor_microservices_logic():
    from actors.cat_agent import act as cat_act, Observation as CatObs
    from actors.mouse_agent import act as mouse_act, Observation as MouseObs

    # Test Cat Agent decisions
    cat_obs = CatObs(
        tick=10,
        round=5,
        self={"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "north"},
        visible_entities=[{"id": "mouse-1", "type": "mouse", "position": {"x": 10, "y": 9}}],
        legal_actions=["north", "south", "east", "west", "stay", "attack"],
    )
    cat_res = cat_act(cat_obs)
    # Cat sees mouse in front within strike range -> should attack
    assert cat_res.action == "attack"

    # Test Mouse Agent decisions
    mouse_obs = MouseObs(
        tick=10,
        round=5,
        self={"id": "mouse-1", "position": {"x": 10, "y": 9}, "facing": "south"},
        visible_entities=[{"id": "cat-1", "type": "cat", "position": {"x": 10, "y": 10}}],
        legal_actions=["north", "south", "east", "west", "stay"],
    )
    mouse_res = mouse_act(mouse_obs)
    # Mouse sees cat to the south -> should flee north
    assert mouse_res.action == "north"

