from typing import Dict, Optional
from fastapi import APIRouter, HTTPException
from app.game.simulation import Simulation
from app.models.simulation import (
    ModifyPopulationRequest,
    SimulationConfig,
    SimulationStateResponse,
    UpdateTickRateRequest,
)

router = APIRouter(prefix="/api/simulations", tags=["Admin"])

# In-memory simulations store
simulations: Dict[str, Simulation] = {}
default_sim_id: Optional[str] = None


def get_or_create_default_sim() -> Simulation:
    global default_sim_id
    if default_sim_id is None or default_sim_id not in simulations:
        sim = Simulation()
        simulations[sim.simulation_id] = sim
        default_sim_id = sim.simulation_id
    return simulations[default_sim_id]


def get_simulation_or_404(sim_id: str) -> Simulation:
    if sim_id == "active" or sim_id == "default":
        return get_or_create_default_sim()
    if sim_id not in simulations:
        raise HTTPException(status_code=404, detail=f"Simulation '{sim_id}' not found")
    return simulations[sim_id]


@router.post("", response_model=SimulationStateResponse)
async def create_simulation(config: Optional[SimulationConfig] = None):
    global default_sim_id
    sim = Simulation(config=config)
    simulations[sim.simulation_id] = sim
    default_sim_id = sim.simulation_id
    return sim.get_state()


@router.get("/active", response_model=SimulationStateResponse)
async def get_active_simulation_state():
    sim = get_or_create_default_sim()
    return sim.get_state()


@router.get("/{sim_id}/state", response_model=SimulationStateResponse)
async def get_simulation_state(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return sim.get_state()


@router.post("/{sim_id}/start", response_model=SimulationStateResponse)
async def start_simulation(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return await sim.start()


@router.post("/{sim_id}/pause", response_model=SimulationStateResponse)
async def pause_simulation(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return await sim.pause()


@router.post("/{sim_id}/resume", response_model=SimulationStateResponse)
async def resume_simulation(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return await sim.resume()


@router.post("/{sim_id}/step", response_model=SimulationStateResponse)
async def step_simulation(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return await sim.step()


@router.post("/{sim_id}/step_back", response_model=SimulationStateResponse)
async def step_back_simulation(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return await sim.step_back()


@router.post("/{sim_id}/stop", response_model=SimulationStateResponse)
async def stop_simulation(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return await sim.stop()


@router.post("/{sim_id}/reset", response_model=SimulationStateResponse)
async def reset_simulation(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return await sim.reset()


@router.post("/{sim_id}/tick_rate")
async def update_tick_rate(sim_id: str, req: UpdateTickRateRequest):
    sim = get_simulation_or_404(sim_id)
    new_rate = sim.set_tick_rate(req.tick_rate)
    return {"tick_rate": new_rate}


@router.post("/{sim_id}/entities")
async def modify_entities(sim_id: str, req: ModifyPopulationRequest):
    sim = get_simulation_or_404(sim_id)
    try:
        new_count = sim.modify_population(req.species, req.delta)
        return {"species": req.species, "new_count": new_count}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
