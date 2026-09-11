import logging
import math
import random
from typing import List, Optional
import httpx
from actors.cat_agent import act as cat_agent_act, Observation as CatObservation
from actors.mouse_agent import act as mouse_agent_act, Observation as MouseObservation
from app.game.actions import ActionHandler
from app.game.entities import EntityManager
from app.game.world import World
from app.models.action import ActionType
from app.models.entity import Direction, Entity, EntityType, Position
from app.models.simulation import GameEvent

logger = logging.getLogger("simulation.events")


class Scheduler:
    def __init__(self, world: World, entity_manager: EntityManager, action_handler: ActionHandler):
        self.world = world
        self.entity_manager = entity_manager
        self.action_handler = action_handler
        self.tick: int = 0
        self.round: int = 0
        self.captures: int = 0
        self.events: List[GameEvent] = []

    def step_logical_clock(self) -> int:
        self.tick += 1
        active_count = len(self.entity_manager.get_alive_entities())
        if active_count > 0:
            self.round = self.tick // max(1, active_count)
        else:
            self.round = self.tick // 10
        return self.tick

    def _build_observation_payload(self, entity: Entity) -> dict:
        species_key = entity.type.value
        vision_radius = 4
        if species_key in self.entity_manager.species_config:
            vision_radius = self.entity_manager.species_config[species_key].vision_radius

        visible = []
        if entity.type == EntityType.CAT:
            fov_tiles = set(self.world.get_forward_fov_tiles(entity.position, entity.facing, distance=vision_radius))
            for other in self.entity_manager.get_alive_entities():
                if other.id != entity.id and (other.position.x, other.position.y) in fov_tiles:
                    visible.append({
                        "id": other.id,
                        "type": other.type.value,
                        "position": {"x": other.position.x, "y": other.position.y},
                        "facing": other.facing.value,
                    })
        else:
            for other in self.entity_manager.get_alive_entities():
                if other.id == entity.id:
                    continue
                dist_sq = (other.position.x - entity.position.x)**2 + (other.position.y - entity.position.y)**2
                if dist_sq <= vision_radius**2:
                    visible.append({
                        "id": other.id,
                        "type": other.type.value,
                        "position": {"x": other.position.x, "y": other.position.y},
                        "facing": other.facing.value,
                    })

        allow_attack = (entity.type == EntityType.CAT)
        legal_actions = [a.value for a in self.world.get_legal_actions(entity.position, allow_attack=allow_attack)]
        return {
            "tick": self.tick,
            "round": self.round,
            "self": {
                "id": entity.id,
                "type": entity.type.value,
                "position": {"x": entity.position.x, "y": entity.position.y},
                "facing": entity.facing.value,
            },
            "visible_entities": visible,
            "legal_actions": legal_actions,
        }

    async def execute_simulation_step(self, skip_entity_id: Optional[str] = None) -> int:
        self.step_logical_clock()

        entities = self.entity_manager.get_alive_entities()
        cats = [e for e in entities if e.type == EntityType.CAT and e.alive]
        mice = [e for e in entities if e.type == EntityType.MOUSE and e.alive]
        random.shuffle(cats)
        random.shuffle(mice)

        # 1. Cats turn
        for cat in cats:
            if not cat.alive or cat.id == skip_entity_id:
                continue

            mode = self.entity_manager.get_agent_mode(cat.id)
            if mode == "manual":
                # Controlled manually by human player via UI / HTTP
                continue

            action_val = None
            if mode == "http":
                agent_url = self.entity_manager.get_agent_url(cat.id)
                if agent_url:
                    try:
                        obs = self._build_observation_payload(cat)
                        async with httpx.AsyncClient(timeout=1.5) as client:
                            resp = await client.post(agent_url, json=obs)
                            if resp.status_code == 200:
                                data = resp.json()
                                action_val = data.get("action")
                                thought = data.get("thought", "")
                                print(f"🤖 [HTTP AGENT {cat.id} @ Tick {self.tick}] -> {action_val} | Thought: \"{thought}\"", flush=True)
                    except Exception as err:
                        print(f"⚠️ [HTTP AGENT {cat.id}] Error contacting {agent_url}: {err}", flush=True)

            # In auto mode (or if HTTP fallback needed), execute cat_agent brain
            if not action_val:
                obs_payload = self._build_observation_payload(cat)
                cat_obs = CatObservation(**obs_payload)
                act_res = cat_agent_act(cat_obs)
                action_val = act_res.action

            if action_val in [a.value for a in ActionType]:
                res = self.action_handler.apply_action(cat, ActionType(action_val), self.tick)
                if res.action == ActionType.ATTACK and res.affected_entities:
                    self.captures += len(res.affected_entities)
                    for victim_id in res.affected_entities:
                        victim = self.entity_manager.entities.get(victim_id)
                        v_pos = f"({victim.position.x}, {victim.position.y})" if victim else "?"
                        msg = f"💥 [TICK {self.tick} | R{self.round}] CAPTURE: {cat.id} at ({cat.position.x}, {cat.position.y}) facing {cat.facing.value.upper()} attacked and captured {victim_id} at {v_pos}! (Total captures: {self.captures})"
                        logger.info(msg)
                        print(msg, flush=True)
                        self.events.append(GameEvent(
                            tick=self.tick,
                            round=self.round,
                            event_type="capture",
                            actor_id=cat.id,
                            target_id=victim_id,
                            position=victim.position if victim else cat.position,
                            message=msg,
                        ))

        # 2. Mice turn
        for mouse in mice:
            if not mouse.alive or mouse.id == skip_entity_id:
                continue

            mode = self.entity_manager.get_agent_mode(mouse.id)
            if mode == "manual":
                # Controlled manually by human player via UI / HTTP
                continue

            action_val = None
            if mode == "http":
                agent_url = self.entity_manager.get_agent_url(mouse.id)
                if agent_url:
                    try:
                        obs = self._build_observation_payload(mouse)
                        async with httpx.AsyncClient(timeout=1.5) as client:
                            resp = await client.post(agent_url, json=obs)
                            if resp.status_code == 200:
                                data = resp.json()
                                action_val = data.get("action")
                                thought = data.get("thought", "")
                                print(f"🤖 [HTTP AGENT {mouse.id} @ Tick {self.tick}] -> {action_val} | Thought: \"{thought}\"", flush=True)
                    except Exception as err:
                        print(f"⚠️ [HTTP AGENT {mouse.id}] Error contacting {agent_url}: {err}", flush=True)

            # In auto mode (or if HTTP fallback needed), execute mouse_agent brain
            if not action_val:
                obs_payload = self._build_observation_payload(mouse)
                mouse_obs = MouseObservation(**obs_payload)
                act_res = mouse_agent_act(mouse_obs)
                action_val = act_res.action


            if action_val in [a.value for a in ActionType]:
                self.action_handler.apply_action(mouse, ActionType(action_val), self.tick)

            # Check for bump / displacement collision with Cats
            overlapping_cats = [
                c for c in self.entity_manager.get_alive_entities()
                if c.type == EntityType.CAT and c.position.x == mouse.position.x and c.position.y == mouse.position.y
            ]
            if overlapping_cats:
                cat_threat = overlapping_cats[0]
                old_x, old_y = mouse.position.x, mouse.position.y
                self.entity_manager.displace_entity(mouse, bounce_dist=2)
                msg = f"🔄 [TICK {self.tick} | R{self.round}] BUMP: {mouse.id} collided with {cat_threat.id} at ({old_x}, {old_y}) -> bounced to ({mouse.position.x}, {mouse.position.y}) (facing {mouse.facing.value.upper()})"
                logger.info(msg)
                print(msg, flush=True)
                self.events.append(GameEvent(
                    tick=self.tick,
                    round=self.round,
                    event_type="bump",
                    actor_id=mouse.id,
                    target_id=cat_threat.id,
                    position=mouse.position,
                    message=msg,
                ))

        return self.tick
