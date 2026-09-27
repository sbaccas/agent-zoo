from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from app.game.heatmap import DEFAULT_GRID_SIZE, MAX_GRID_SIZE, MIN_GRID_SIZE
from app.game.simulation import Simulation
from app.models.simulation import (
    DisclosureConfig,
    GameEvent,
    ModifyPopulationRequest,
    RepositionEntityRequest,
    SimulationConfig,
    SimulationStateResponse,
    SpeciesConfig,
    SeekSimulationRequest,
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


@router.post("/{sim_id}/seek", response_model=SimulationStateResponse)
async def seek_simulation(sim_id: str, req: SeekSimulationRequest):
    """Seek/rewind the simulation state back to a historical tick."""
    sim = get_simulation_or_404(sim_id)
    return await sim.seek_to_tick(req.tick)


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


@router.post("/{sim_id}/entities/{entity_id}/reposition")
async def reposition_entity(
    sim_id: str,
    entity_id: str,
    req: RepositionEntityRequest,
):
    """Stage an entity to be repositioned on the next simulation tick."""
    sim = get_simulation_or_404(sim_id)
    try:
        result = sim.stage_reposition(entity_id, req.x, req.y)
        return {
            "status": "success",
            **result,
            "state": sim.get_state(),
        }
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


@router.get("/{sim_id}/events")
async def get_events(
    sim_id: str,
    limit: Optional[int] = Query(None, ge=1, le=1000),
    since_tick: Optional[int] = Query(None, ge=0),
    actor_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
):
    """Retrieve structured game events and death annals for Game Stats."""
    sim = get_simulation_or_404(sim_id)
    all_events = list(sim.scheduler.events)
    if since_tick is not None:
        all_events = [e for e in all_events if e.tick >= since_tick]
    if actor_id:
        all_events = [e for e in all_events if e.actor_id == actor_id or e.target_id == actor_id]
    if event_type:
        all_events = [e for e in all_events if e.event_type == event_type]
    if limit is not None:
        all_events = all_events[-limit:]

    history_ticks = [snap.tick for snap in sim.history]
    return {
        "simulation_id": sim.simulation_id,
        "tick": sim.scheduler.tick,
        "round": sim.scheduler.round,
        "events": [e.model_dump() for e in all_events],
        "deaths": [dict(d) for d in sim.scheduler.deaths],
        "entity_events": {k: [dict(ev) for ev in v] for k, v in sim.scheduler.entity_events.items()},
        "can_step_back": len(sim.history) > 0,
        "history_min_tick": min(history_ticks) if history_ticks else sim.scheduler.tick,
        "history_max_tick": max(history_ticks) if history_ticks else sim.scheduler.tick,
        "history_length": len(history_ticks),
    }


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


@router.get("/{sim_id}/comms")
async def get_simulation_comms(sim_id: str, limit: int = Query(50, ge=1, le=200)):
    """Returns the rolling inter-cat radio transmission log for this simulation."""
    sim = get_simulation_or_404(sim_id)
    return {"transmissions": sim.get_comms_log(limit=limit)}


@router.post("/{sim_id}/comms")
async def report_simulation_comms(sim_id: str, payload: Dict[str, Any]):
    """Receives A2A transmission telemetry from participating agents."""
    sim = get_simulation_or_404(sim_id)
    sender = payload.get("sender_id", "unknown")
    recipient = payload.get("recipient_id", "*")
    msg = payload.get("message", "")
    tick = payload.get("tick") or sim.scheduler.tick
    round_num = payload.get("round") or sim.scheduler.round
    m_type = payload.get("type", "DIRECT_MESSAGE")

    msg_id = payload.get("message_id")
    # Deduplicate: avoid appending duplicate transmission or duplicate game event
    if msg_id and any(t.get("message_id") == msg_id for t in sim.scheduler.transmissions):
        return {"status": "ok", "recorded": False, "duplicate": True}
    if not msg_id and any(
        t.get("sender_id") == sender
        and t.get("recipient_id") == recipient
        and t.get("message") == msg
        and t.get("tick") == tick
        for t in sim.scheduler.transmissions[-20:]
    ):
        return {"status": "ok", "recorded": False, "duplicate": True}

    record = {
        "tick": tick,
        "round": round_num,
        "sender_id": sender,
        "recipient_id": recipient,
        "message": msg,
        "type": m_type,
        "protocol": payload.get("protocol", "a2a/v1"),
        "message_id": msg_id,
    }
    sim.scheduler.transmissions.append(record)
    if len(sim.scheduler.transmissions) > 200:
        sim.scheduler.transmissions.pop(0)

    is_dm = recipient and recipient not in ("*", "broadcast", "all")
    tag = f"A2A DM: {sender} -> {recipient}" if is_dm else f"A2A BROADCAST: {sender}"
    sim.scheduler.events.append(GameEvent(
        tick=tick,
        round=round_num,
        event_type="a2a_message",
        actor_id=sender,
        target_id=recipient if is_dm else None,
        message=f"📻 [TICK {tick} | R{round_num}] {tag}: \"{msg}\"",
        details=record,
    ))
    return {"status": "ok", "recorded": True}


