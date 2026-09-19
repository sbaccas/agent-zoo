from typing import Dict, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from app.game.heatmap import DEFAULT_GRID_SIZE, MAX_GRID_SIZE, MIN_GRID_SIZE
from app.game.simulation import Simulation
from app.models.simulation import (
    DisclosureConfig,
    ModifyPopulationRequest,
    SimulationConfig,
    SimulationStateResponse,
    SpeciesConfig,
    UpdateDisclosureConfigRequest,
    UpdateSpeciesConfigRequest,
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


@router.get("/{sim_id}/boxscore")
async def get_boxscore(sim_id: str):
    """Per-cat KPIs for the box score window.

    Read-only and cheap: the counters are maintained during play, so this only
    formats them and can safely be polled by the UI.
    """
    sim = get_simulation_or_404(sim_id)
    return sim.get_boxscore()


@router.get("/{sim_id}/heatmap")
async def get_heatmap(
    sim_id: str,
    grid: int = Query(
        DEFAULT_GRID_SIZE,
        ge=MIN_GRID_SIZE,
        le=MAX_GRID_SIZE,
        description="Cells per side, e.g. 5 draws a 5x5 map",
    ),
    last: Optional[int] = Query(None, ge=1, description="Restrict to the last N ticks"),
):
    """Per-cat movement map: where each cat spent its ticks.

    Derived on request from the step-back snapshots rather than polled, so it is
    heavier than the box score and the UI fetches it on demand rather than every
    two seconds.
    """
    sim = get_simulation_or_404(sim_id)
    if getattr(sim.config, "disclosure", None) and not sim.config.disclosure.coach_sees_coverage_map:
        raise HTTPException(
            status_code=403,
            detail="Coverage map disclosure is disabled (coach_sees_coverage_map=False)",
        )
    return sim.get_heatmap(grid_size=grid, last_n=last)


@router.get("/{sim_id}/heatmap.txt", response_class=PlainTextResponse)
async def get_heatmap_text(
    sim_id: str,
    grid: int = Query(DEFAULT_GRID_SIZE, ge=MIN_GRID_SIZE, le=MAX_GRID_SIZE),
    last: Optional[int] = Query(None, ge=1),
):
    """The same map as plain text, for a reader with a context window.

    Exists so the coach can spend one tool call and paste the result straight
    into a prompt, rather than fetching JSON it would only have to re-render.
    """
    sim = get_simulation_or_404(sim_id)
    if getattr(sim.config, "disclosure", None) and not sim.config.disclosure.coach_sees_coverage_map:
        raise HTTPException(
            status_code=403,
            detail="Coverage map disclosure is disabled (coach_sees_coverage_map=False)",
        )
    return sim.get_heatmap(grid_size=grid, last_n=last)["ascii"]



@router.get("/{sim_id}/species", response_model=Dict[str, SpeciesConfig])
async def get_species_configs(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return sim.entity_manager.species_config


@router.patch("/{sim_id}/species/{species_key}", response_model=SpeciesConfig)
async def update_species_config(sim_id: str, species_key: str, req: UpdateSpeciesConfigRequest):
    sim = get_simulation_or_404(sim_id)
    key = species_key.lower()
    if key not in sim.entity_manager.species_config:
        raise HTTPException(status_code=404, detail=f"Species '{species_key}' not found in simulation")

    current = sim.entity_manager.species_config[key]
    updates = req.model_dump(exclude_unset=True)
    for field, val in updates.items():
        if val is not None:
            setattr(current, field, val)

    # Keep config model synchronized
    sim.config.species[key] = current
    return current


@router.get("/{sim_id}/disclosure", response_model=DisclosureConfig)
async def get_disclosure_config(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    return sim.config.disclosure


@router.patch("/{sim_id}/disclosure", response_model=DisclosureConfig)
async def update_disclosure_config(sim_id: str, req: UpdateDisclosureConfigRequest):
    sim = get_simulation_or_404(sim_id)
    current = sim.config.disclosure
    updates = req.model_dump(exclude_unset=True)
    for field, val in updates.items():
        if val is not None:
            setattr(current, field, val)
    return current


