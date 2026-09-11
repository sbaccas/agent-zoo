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
    assert state.height == 50
    assert state.cat_count == 5
    assert state.mouse_count == 20


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
