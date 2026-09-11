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
