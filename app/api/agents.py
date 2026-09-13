import logging
from typing import List
from fastapi import APIRouter, HTTPException
from app.api.admin import get_simulation_or_404
from app.models.action import ActionRequest, ActionResult, ActionType, ObservationResponse, RegisterAgentRequest
from app.models.entity import EntityType
from app.models.simulation import GameEvent, SimulationStatus

logger = logging.getLogger("api.agents")
router = APIRouter(prefix="/api/simulations/{sim_id}/agents", tags=["Agents"])


@router.get("", response_model=List[dict])
async def list_agents(sim_id: str):
    sim = get_simulation_or_404(sim_id)
    agents = []
    for e in sim.entity_manager.get_alive_entities():
        agents.append({
            "id": e.id,
            "type": e.type,
            "position": e.position,
            "facing": e.facing,
            "mode": sim.entity_manager.get_agent_mode(e.id),
            "agent_url": sim.entity_manager.get_agent_url(e.id),
        })
    return agents


@router.post("/{agent_id}/register")
async def register_agent(sim_id: str, agent_id: str, req: RegisterAgentRequest):
    sim = get_simulation_or_404(sim_id)
    if agent_id not in sim.entity_manager.entities:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    from app.game.ws_manager import ws_agent_manager
    effective_mode = req.mode
    if effective_mode == "auto" and ws_agent_manager.has_agent(agent_id):
        effective_mode = "ws"

    sim.entity_manager.set_agent_mode(agent_id, effective_mode, req.agent_url)
    return {
        "agent_id": agent_id,
        "mode": effective_mode,
        "agent_url": req.agent_url,
        "message": f"Agent {agent_id} configured as {effective_mode}",
    }


@router.get("/{agent_id}/observation", response_model=ObservationResponse)
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

    # Calculate visible entities based on predator vs prey vision models:
    visible = []
    if entity.type == EntityType.CAT:
        # Predator: forward-facing vision cone
        fov_tiles = set(sim.world.get_forward_fov_tiles(entity.position, entity.facing, distance=vision_radius))
        for other in sim.entity_manager.get_alive_entities():
            if other.id != entity.id and (other.position.x, other.position.y) in fov_tiles:
                visible.append(other)
    else:
        # Prey: 360-degree panoramic vision (eyes on sides)
        for other in sim.entity_manager.get_alive_entities():
            if other.id == entity.id:
                continue
            dist_sq = (other.position.x - entity.position.x) ** 2 + (
                other.position.y - entity.position.y
            ) ** 2
            if dist_sq <= vision_radius ** 2:
                visible.append(other)

    allow_attack = (entity.type == EntityType.CAT)
    legal_actions = sim.world.get_legal_actions(entity.position, allow_attack=allow_attack, facing=entity.facing)

    spec_cfg = sim.entity_manager.species_config.get(species_key)
    attack_range = spec_cfg.attack_range if spec_cfg else (2 if entity.type == EntityType.CAT else 0)
    capabilities = {
        "vision_radius": vision_radius,
        "attack_range": attack_range,
        "move_cooldown": spec_cfg.move_cooldown if spec_cfg else 1,
        "attack_cooldown": spec_cfg.attack_cooldown if spec_cfg else 0,
        "lunge_cooldown": spec_cfg.lunge_cooldown if spec_cfg else 0,
        "weight": spec_cfg.weight if spec_cfg else 1,
    }

    return ObservationResponse(
        simulation_id=sim.simulation_id,
        tick=sim.scheduler.tick,
        round=sim.scheduler.round,
        self_entity=entity,
        visible_entities=visible,
        legal_actions=legal_actions,
        capabilities=capabilities,
    )


@router.post("/{agent_id}/actions", response_model=ActionResult)
async def submit_agent_action(sim_id: str, agent_id: str, req: ActionRequest, step: bool = True):
    sim = get_simulation_or_404(sim_id)
    if agent_id not in sim.entity_manager.entities:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    entity = sim.entity_manager.entities[agent_id]
    if not entity.alive:
        raise HTTPException(status_code=410, detail=f"Agent '{agent_id}' is dead")

    # 1. Apply player's intended action (fails if entity is in animation cooldown lock)
    result = sim.action_handler.apply_action(entity, req.action, sim.scheduler.tick)

    # 2. If action resulted in captures, update counters and log event
    if result.action == ActionType.ATTACK and result.affected_entities:
        sim.scheduler.captures += len(result.affected_entities)
        for victim_id in result.affected_entities:
            victim = sim.entity_manager.entities.get(victim_id)
            v_pos = f"({victim.position.x}, {victim.position.y})" if victim else "?"
            msg = f"💥 [TICK {sim.scheduler.tick} | R{sim.scheduler.round}] MANUAL CAPTURE: {entity.id} at ({entity.position.x}, {entity.position.y}) attacked and captured {victim_id} at {v_pos}!"
            logger.info(msg)
            print(msg, flush=True)
            sim.scheduler.events.append(GameEvent(
                tick=sim.scheduler.tick,
                round=sim.scheduler.round,
                event_type="capture",
                actor_id=entity.id,
                target_id=victim_id,
                position=victim.position if victim else entity.position,
                message=msg,
            ))

    # 3. Synchronize simulation clock: advance world 1 tick so other animals react (only if action was accepted)
    if step and sim.status != SimulationStatus.RUNNING and result.accepted:
        sim._save_snapshot()
        await sim.scheduler.execute_simulation_step(skip_entity_id=agent_id)

    return result

