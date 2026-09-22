import logging
from typing import List
from fastapi import APIRouter, HTTPException
from app.api.admin import get_simulation_or_404
from app.game.world import World
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

    species_key = entity.type.value
    vision_radius = 4
    vision_rear = World.FOV_REAR_DEPTH
    vision_half_width = World.FOV_HALF_WIDTH
    if species_key in sim.config.species:
        _cfg = sim.config.species[species_key]
        vision_radius = _cfg.vision_radius
        vision_rear = _cfg.vision_rear
        vision_half_width = _cfg.vision_half_width

    # Calculate visible entities based on predator vs prey vision models:
    visible = []
    if entity.type == EntityType.CAT:
        # Predator: a box around the cat, biased forward. Not a cone -- that
        # shape was removed; see World.get_fov_tiles for why.
        fov_tiles = set(sim.world.get_fov_tiles(
            entity.position,
            entity.facing,
            distance=vision_radius,
            rear=vision_rear,
            half_width=vision_half_width,
        ))
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
    if entity.alive:
        legal_actions = sim.world.get_legal_actions(entity.position, allow_attack=allow_attack, facing=entity.facing)
    else:
        legal_actions = []

    spec_cfg = sim.entity_manager.species_config.get(species_key)
    attack_range = spec_cfg.attack_range if spec_cfg else (2 if entity.type == EntityType.CAT else 0)
    capabilities = {
        "vision_radius": vision_radius,
        "vision_rear": vision_rear,
        "vision_half_width": vision_half_width,
        "attack_range": attack_range,
        "move_cooldown": spec_cfg.move_cooldown if spec_cfg else 1,
        "attack_cooldown": spec_cfg.attack_cooldown if spec_cfg else 0,
        "lunge_cooldown": spec_cfg.lunge_cooldown if spec_cfg else 0,
        "weight": spec_cfg.weight if spec_cfg else 1,
        "aim_assist": bool(spec_cfg.aim_assist) if spec_cfg else False,
        "hp": entity.hp,
        "max_hp": entity.max_hp,
        "attack_power": spec_cfg.attack_power if spec_cfg else (34 if entity.type == EntityType.CAT else 0),
    }

    return ObservationResponse(
        simulation_id=sim.simulation_id,
        tick=sim.scheduler.tick,
        round=sim.scheduler.round,
        self_entity=entity,
        visible_entities=visible,
        legal_actions=legal_actions,
        capabilities=capabilities,
        score=sim.scheduler.scores.get(agent_id, 0),
        scoreboard=sim.scheduler.get_scoreboard(),
        deaths=[dict(d) for d in sim.scheduler.deaths],
        recent_deaths=[dict(d) for d in sim.scheduler.deaths[-5:]],
    )


@router.post("/{agent_id}/actions", response_model=ActionResult)
async def submit_agent_action(sim_id: str, agent_id: str, req: ActionRequest, step: bool = True):
    sim = get_simulation_or_404(sim_id)
    if agent_id not in sim.entity_manager.entities:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    entity = sim.entity_manager.entities[agent_id]
    if not entity.alive:
        raise HTTPException(status_code=410, detail=f"Agent '{agent_id}' is dead")

    # 1. Action lock check: If agent is currently on cooldown, reject without advancing clock
    if entity.action_cooldown > 0:
        return sim.action_handler.apply_action(entity, req.action, sim.scheduler.tick)

    # 2. Advance clock and save snapshot if stepping simulation
    if step and sim.status != SimulationStatus.RUNNING:
        sim._save_snapshot()
        sim.scheduler.step_logical_clock()

    # 3. Apply player's intended action at the synchronized tick
    result = sim.action_handler.apply_action(entity, req.action, sim.scheduler.tick)

    # 4. If action was an attack, log slash event and any captures at the synchronized tick
    if result.action == ActionType.ATTACK and result.accepted:
        slash_details = result.details or {}
        sim.scheduler.events.append(GameEvent(
            tick=sim.scheduler.tick,
            round=sim.scheduler.round,
            event_type="slash",
            actor_id=entity.id,
            position=entity.position,
            message=f"⚔️ [TICK {sim.scheduler.tick} | R{sim.scheduler.round}] SLASH: {entity.id} executed slash attack ({len(result.affected_entities)} hits)",
            details=slash_details,
        ))
        # Routed through the scheduler helper so a manual kill scores exactly
        # like an agent's. This path previously bumped the global `captures`
        # counter without ever touching `scores`, so manual play inflated the
        # total while leaving the cat's own leaderboard entry untouched.
        for victim_id in result.affected_entities:
            victim = sim.entity_manager.entities.get(victim_id)
            if victim:
                sim.scheduler._register_capture(entity.id, victim, "manual slash impact")

    # 5. Synchronize simulation clock: advance other animals for this step (clock was already advanced)
    if step and sim.status != SimulationStatus.RUNNING and result.accepted:
        await sim.scheduler.execute_simulation_step(skip_entity_id=agent_id, advance_clock=False)

    return result

