import pytest
from app.game.simulation import Simulation
from app.models.simulation import SimulationConfig, SimulationStatus


@pytest.mark.asyncio
async def test_simulation_creation():
    config = SimulationConfig(
        width=50,
        height=50,
        populations={"cats": 5, "mice": 20},
    )
    sim = Simulation(config=config)
    state = sim.get_state()
    assert state.status == SimulationStatus.CREATED
    assert state.tick == 0
    assert state.round == 0
    assert state.width == 50
    assert state.cat_count == 5
    assert state.mouse_count == 20
    assert state.species is not None
    assert "cat" in state.species
    assert "mouse" in state.species
    assert state.species["cat"].attack_range == 2
    assert state.species["cat"].vision_radius == 10
    assert state.species["cat"].move_cooldown == 3
    assert state.species["mouse"].move_cooldown == 2


@pytest.mark.asyncio
async def test_simulation_step():
    sim = Simulation()
    assert sim.tick == 0
    await sim.step()
    assert sim.tick == 1
    await sim.step()
    assert sim.tick == 2


@pytest.mark.asyncio
async def test_simulation_population_modification():
    sim = Simulation(config=SimulationConfig(populations={"cats": 2, "mice": 5}))
    sim.modify_population("cat", 3)
    assert sim.entity_manager.count_by_type("cat") == 5

    sim.modify_population("mouse", -2)
    assert sim.entity_manager.count_by_type("mouse") == 3


@pytest.mark.asyncio
async def test_simulation_reset():
    sim = Simulation()
    await sim.step()
    await sim.step()
    assert sim.tick == 2

    await sim.reset()
    state = sim.get_state()
    assert state.tick == 0
    assert state.status == SimulationStatus.CREATED


@pytest.mark.asyncio
async def test_default_population_is_small():
    sim = Simulation()
    state = sim.get_state()
    assert state.cat_count == 2
    assert state.mouse_count == 10


@pytest.mark.asyncio
async def test_bump_displacement():
    from app.models.entity import Direction, EntityType, Position
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=10, y=10), facing=Direction.WEST)

    # Overlapping tile: displace mouse
    sim.entity_manager.displace_entity(mouse, bounce_dist=2)
    assert mouse.alive
    assert (mouse.position.x, mouse.position.y) != (cat.position.x, cat.position.y)


@pytest.mark.asyncio
async def test_cat_slash_attack_captures_mouse():
    from app.models.action import ActionType
    from app.models.entity import Direction, EntityType, Position
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    # Mouse right in front of cat's face
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10), facing=Direction.WEST)

    res = sim.action_handler.apply_action(cat, ActionType.ATTACK, current_tick=1)
    assert res.accepted
    assert mouse.id in res.affected_entities
    assert not mouse.alive


@pytest.mark.asyncio
async def test_step_back_time_travel():
    sim = Simulation()
    assert sim.tick == 0
    state_0 = sim.get_state()
    initial_positions = {e.id: (e.position.x, e.position.y) for e in state_0.entities}

    # Step forward 2 ticks
    await sim.step()
    assert sim.tick == 1
    await sim.step()
    assert sim.tick == 2

    # Step back 1 tick
    state_1 = await sim.step_back()
    assert state_1.tick == 1

    # Step back to 0
    state_restored_0 = await sim.step_back()
    assert state_restored_0.tick == 0
    restored_positions = {e.id: (e.position.x, e.position.y) for e in state_restored_0.entities}
    assert restored_positions == initial_positions


@pytest.mark.asyncio
async def test_manual_agent_skips_auto_movement():
    from app.models.entity import Direction, EntityType, Position
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    sim.entity_manager.set_agent_mode(cat.id, "manual")

    # In manual mode, scheduler should not move cat during step
    await sim.step()
    assert cat.position.x == 10
    assert cat.position.y == 10


@pytest.mark.asyncio
async def test_http_agent_dispatch_live():
    from unittest.mock import AsyncMock, patch
    from app.models.entity import Direction, EntityType, Position
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    sim.entity_manager.set_agent_mode(cat.id, "http", "http://127.0.0.1:8001/act")

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"action": "north", "thought": "Hunting northward"}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        await sim.step()
        assert cat.position.y == 9  # moved north


@pytest.mark.asyncio
async def test_cat_lunge_jumps_over_mouse():
    from app.models.action import ActionType
    from app.models.entity import Direction, EntityType, Position
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    # Cat at (10, 10) facing EAST
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    # Mouse at (11, 10) directly 1 square ahead
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10), facing=Direction.WEST)

    # Cat executes lunge -> lands at (12, 10), jumping right over mouse at (11, 10)
    res = sim.action_handler.apply_action(cat, ActionType.LUNGE, current_tick=1)
    assert res.accepted is True
    assert cat.position.x == 12
    assert cat.position.y == 10
    assert cat.facing == Direction.EAST

    # Mouse is untouched and alive
    assert mouse.alive is True
    assert mouse.position.x == 11
    assert mouse.position.y == 10


@pytest.mark.asyncio
async def test_mouse_cannot_lunge():
    from app.models.action import ActionType
    from app.models.entity import Direction, EntityType, Position
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=10, y=10), facing=Direction.EAST)

    # Mouse legal actions should NOT have LUNGE
    legal = sim.world.get_legal_actions(mouse.position, allow_attack=False, facing=mouse.facing)
    assert ActionType.LUNGE not in legal

    # Attempting lunge fails
    res = sim.action_handler.apply_action(mouse, ActionType.LUNGE, current_tick=1)
    assert res.accepted is False


@pytest.mark.asyncio
async def test_pacing_mouse_moves_1_tile_every_2_ticks():
    """Verifies that mice move at a pace of 1 tile every 2 ticks (move_cooldown = 2)."""
    from app.models.action import ActionType
    from app.models.entity import Direction, EntityType, Position
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=10, y=10), facing=Direction.EAST)

    # Initial state
    assert mouse.position.x == 10
    assert mouse.action_cooldown == 0

    # Tick 1: Mouse moves East
    res1 = sim.action_handler.apply_action(mouse, ActionType.EAST, current_tick=1)
    assert res1.accepted is True
    assert mouse.position.x == 11
    assert mouse.action_cooldown == 1  # 2 ticks total: tick 1 active, 1 tick recovery
    assert mouse.locked_action == "east"

    # Tick 2: Mouse is animation-locked, cannot act
    res_locked = sim.action_handler.apply_action(mouse, ActionType.EAST, current_tick=2)
    assert res_locked.accepted is False
    assert "locked in action 'east'" in res_locked.message

    # Scheduler steps logical clock to tick 2: cooldown decrements 1 -> 0
    await sim.step()
    assert mouse.position.x == 11  # Still at tile (11, 10), did not move on tick 2
    assert mouse.action_cooldown == 0
    assert mouse.locked_action is None

    # Tick 3: Cooldown is 0, mouse is ready to move again!
    res3 = sim.action_handler.apply_action(mouse, ActionType.EAST, current_tick=3)
    assert res3.accepted is True
    assert mouse.position.x == 12  # Now at tile (12, 10)
    assert mouse.action_cooldown == 1


@pytest.mark.asyncio
async def test_pacing_cat_moves_1_tile_every_3_ticks():
    """Verifies that cats move at a pace of 1 tile every 3 ticks (move_cooldown = 3)."""
    from app.models.action import ActionType
    from app.models.entity import Direction, EntityType, Position
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)

    # Tick 1: Cat moves East
    res1 = sim.action_handler.apply_action(cat, ActionType.EAST, current_tick=1)
    assert res1.accepted is True
    assert cat.position.x == 11
    assert cat.action_cooldown == 2  # 3 ticks total: tick 1 active, 2 ticks recovery
    assert cat.locked_action == "east"

    # Tick 2: Locked (cooldown 2 -> 1)
    res_locked = sim.action_handler.apply_action(cat, ActionType.EAST, current_tick=2)
    assert res_locked.accepted is False
    await sim.step()
    assert cat.position.x == 11  # Still at tile 11
    assert cat.action_cooldown == 1
    assert cat.locked_action == "east"

    # Tick 3: Still locked (cooldown 1 -> 0)
    res_locked2 = sim.action_handler.apply_action(cat, ActionType.EAST, current_tick=3)
    assert res_locked2.accepted is False
    await sim.step()
    assert cat.position.x == 11  # Still at tile 11
    assert cat.action_cooldown == 0
    assert cat.locked_action is None

    # Tick 4: Cooldown complete, Cat moves again!
    res4 = sim.action_handler.apply_action(cat, ActionType.EAST, current_tick=4)
    assert res4.accepted is True
    assert cat.position.x == 12
    assert cat.action_cooldown == 2


@pytest.mark.asyncio
async def test_hyperparameter_species_config_live_tuning():
    """Verifies that games hyperparameters (cooldowns, attack AoE range) can be customized and tested."""
    from app.models.action import ActionType
    from app.models.entity import Direction, EntityType, Position
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create simulation
        sim_res = await client.post("/api/simulations")
        assert sim_res.status_code == 200
        sim_id = sim_res.json()["simulation_id"]

        # 1. Inspect default hyperparameters via GET /species
        get_res = await client.get(f"/api/simulations/{sim_id}/species")
        assert get_res.status_code == 200
        species_data = get_res.json()
        assert species_data["cat"]["move_cooldown"] == 3
        assert species_data["cat"]["attack_range"] == 2
        assert species_data["cat"]["vision_radius"] == 10
        assert species_data["mouse"]["move_cooldown"] == 2

        # 2. Live tune cat attack_range to 3 tiles and move_cooldown to 1 (fastest)
        patch_res = await client.patch(
            f"/api/simulations/{sim_id}/species/cat",
            json={"attack_range": 3, "move_cooldown": 1},
        )
        assert patch_res.status_code == 200
        updated = patch_res.json()
        assert updated["attack_range"] == 3
        assert updated["move_cooldown"] == 1

        # 3. Test that cat slash AoE now hits target 3 tiles ahead
        from app.api.admin import get_simulation_or_404
        sim = get_simulation_or_404(sim_id)
        cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
        # Mouse 3 squares ahead at (13, 10)
        mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=13, y=10), facing=Direction.WEST)

        slash_res = sim.action_handler.apply_action(cat, ActionType.ATTACK, current_tick=1)
        assert slash_res.accepted is True
        assert mouse.id in slash_res.affected_entities
        assert mouse.alive is False



