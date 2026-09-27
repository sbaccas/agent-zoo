import pytest
from httpx import ASGITransport, AsyncClient
from app.game.simulation import Simulation
from app.main import app
from app.models.entity import Position
from app.models.simulation import SimulationConfig


@pytest.fixture(autouse=True)
def reset_admin_simulations():
    import app.api.admin as admin
    admin.default_sim_id = None
    admin.simulations.clear()


@pytest.mark.asyncio
async def test_stage_reposition_applies_on_next_tick():
    config = SimulationConfig(
        width=50,
        height=50,
        populations={"cats": 1, "mice": 2},
    )
    sim = Simulation(config=config)
    cat = sim.entity_manager.entities.get("cat-1")
    assert cat is not None
    orig_x, orig_y = cat.position.x, cat.position.y

    target_x, target_y = 35, 42
    if orig_x == target_x and orig_y == target_y:
        target_x, target_y = 10, 10

    # 1. Stage reposition
    sim.entity_manager.set_agent_mode("cat-1", "manual")
    res = sim.stage_reposition("cat-1", target_x, target_y)
    assert res["entity_id"] == "cat-1"
    assert res["staged_position"] == {"x": target_x, "y": target_y}
    assert res["effective_tick"] == 1

    # On current tick, position must NOT have changed yet
    assert cat.position.x == orig_x
    assert cat.position.y == orig_y

    state = sim.get_state()
    assert state.pending_repositions is not None
    assert state.pending_repositions.get("cat-1") == {"x": target_x, "y": target_y}

    # 2. Advance to next tick
    await sim.step()
    assert sim.tick == 1

    # Now position MUST be updated
    assert cat.position.x == target_x
    assert cat.position.y == target_y

    # Pending repositions cleared after taking effect
    state_after = sim.get_state()
    assert state_after.pending_repositions is None or len(state_after.pending_repositions) == 0

    # Event logged
    repo_events = [e for e in sim.scheduler.events if e.event_type == "reposition"]
    assert len(repo_events) == 1
    assert repo_events[0].actor_id == "cat-1"
    assert repo_events[0].position.x == target_x
    assert repo_events[0].position.y == target_y


@pytest.mark.asyncio
async def test_stage_reposition_bounds_and_obstacles():
    config = SimulationConfig(
        width=50,
        height=50,
        enable_map_features=True,
        populations={"cats": 1, "mice": 1},
    )
    sim = Simulation(config=config)

    # 1. Out of bounds
    with pytest.raises(ValueError, match="out of world bounds"):
        sim.stage_reposition("cat-1", -1, 10)

    with pytest.raises(ValueError, match="out of world bounds"):
        sim.stage_reposition("cat-1", 10, 100)

    # 2. Unknown entity
    with pytest.raises(ValueError, match="not found"):
        sim.stage_reposition("ghost-99", 10, 10)

    # 3. Obstacle tile (Barn Pantry top-left wall at (15, 15) when enable_map_features is True)
    if sim.world.trees:
        tree = sim.world.trees[0]
        with pytest.raises(ValueError, match="blocked by an obstacle"):
            sim.stage_reposition("cat-1", tree["x"], tree["y"])


@pytest.mark.asyncio
async def test_reposition_api_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Get active sim state
        res_state = await client.get("/api/simulations/active/state")
        assert res_state.status_code == 200
        state = res_state.json()
        # Set cat to manual mode so it stays at the repositioned coordinates
        cat_id = next(e["id"] for e in state["entities"] if e["type"] == "cat")
        await client.post(
            f"/api/simulations/active/agents/{cat_id}/register",
            json={"mode": "manual"},
        )

        # Call reposition endpoint
        repo_res = await client.post(
            f"/api/simulations/active/entities/{cat_id}/reposition",
            json={"x": 22, "y": 33},
        )
        assert repo_res.status_code == 200
        repo_data = repo_res.json()
        assert repo_data["status"] == "success"
        assert repo_data["entity_id"] == cat_id
        assert repo_data["staged_position"] == {"x": 22, "y": 33}

        # Step simulation
        step_res = await client.post("/api/simulations/active/step")
        assert step_res.status_code == 200
        new_state = step_res.json()
        moved_entity = next(e for e in new_state["entities"] if e["id"] == cat_id)
        assert moved_entity["position"]["x"] == 22
        assert moved_entity["position"]["y"] == 33

        # Verify event logged
        repo_ev = next(e for e in new_state["recent_events"] if e["event_type"] == "reposition" and e["actor_id"] == cat_id)
        assert repo_ev["details"]["to"] == {"x": 22, "y": 33}


@pytest.mark.asyncio
async def test_reposition_cancellation_and_rewind():
    config = SimulationConfig(
        width=50,
        height=50,
        populations={"cats": 1, "mice": 1},
    )
    sim = Simulation(config=config)
    cat = sim.entity_manager.entities.get("cat-1")
    orig_x, orig_y = cat.position.x, cat.position.y

    # Staging to different position
    sim.stage_reposition("cat-1", 10, 10)
    assert "cat-1" in sim.scheduler.pending_repositions

    # Dropping back on original spot cancels the pending reposition
    res = sim.stage_reposition("cat-1", orig_x, orig_y)
    assert res.get("cancelled") is True
    assert "cat-1" not in sim.scheduler.pending_repositions

    # Test rewind with pending repositions
    sim.stage_reposition("cat-1", 15, 15)
    await sim.step()
    assert sim.tick == 1
    # Step again
    await sim.step()
    assert sim.tick == 2
    # Step back
    await sim.step_back()
    assert sim.tick == 1

