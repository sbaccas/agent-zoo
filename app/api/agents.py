from fastapi import APIRouter, HTTPException
from app.api.admin import get_simulation_or_404
from app.models.action import ActionRequest, ActionResult
from app.models.entity import Entity

router = APIRouter(prefix="/api/simulations/{sim_id}/agents", tags=["Agents"])


@router.get("/{agent_id}/observation")
async def get_agent_observation(sim_id: str, agent_id: str):
    sim = get_simulation_or_404(sim_id)
    if agent_id not in sim.entity_manager.entities:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    entity = sim.entity_manager.entities[agent_id]
    if not entity.alive:
        raise HTTPException(status_code=410, detail=f"Agent '{agent_id}' is dead")

    species_key = entity.type.value
    vision_radius = 4
    if species_key in sim.config.species:
        vision_radius = sim.config.species[species_key].vision_radius

    # Calculate visible entities using Euclidean or Manhattan distance
    visible = []
    for other in sim.entity_manager.get_alive_entities():
        if other.id == entity.id:
            continue
        dist_sq = (other.position.x - entity.position.x) ** 2 + (
            other.position.y - entity.position.y
        ) ** 2
        if dist_sq <= vision_radius**2:
            visible.append(other)

    legal_actions = sim.world.get_legal_actions(entity.position)

    return {
        "simulation_id": sim.simulation_id,
        "tick": sim.scheduler.tick,
        "round": sim.scheduler.round,
        "self": entity,
        "visible_entities": visible,
        "legal_actions": legal_actions,
    }


@router.post("/{agent_id}/actions", response_model=ActionResult)
async def submit_agent_action(sim_id: str, agent_id: str, req: ActionRequest):
    sim = get_simulation_or_404(sim_id)
    if agent_id not in sim.entity_manager.entities:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    entity = sim.entity_manager.entities[agent_id]
    if not entity.alive:
        raise HTTPException(status_code=410, detail=f"Agent '{agent_id}' is dead")

    result = sim.action_handler.apply_action(entity, req.action, sim.scheduler.tick)
    return result
