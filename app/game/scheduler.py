import logging
import math
import random
from typing import List, Optional
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

    def _get_best_move_towards(self, current: Position, target: Position) -> ActionType:
        dx = target.x - current.x
        dy = target.y - current.y
        legal = self.world.get_legal_actions(current)

        moves = []
        if abs(dx) >= abs(dy):
            if dx > 0 and ActionType.EAST in legal:
                moves.append(ActionType.EAST)
            elif dx < 0 and ActionType.WEST in legal:
                moves.append(ActionType.WEST)
            if dy > 0 and ActionType.SOUTH in legal:
                moves.append(ActionType.SOUTH)
            elif dy < 0 and ActionType.NORTH in legal:
                moves.append(ActionType.NORTH)
        else:
            if dy > 0 and ActionType.SOUTH in legal:
                moves.append(ActionType.SOUTH)
            elif dy < 0 and ActionType.NORTH in legal:
                moves.append(ActionType.NORTH)
            if dx > 0 and ActionType.EAST in legal:
                moves.append(ActionType.EAST)
            elif dx < 0 and ActionType.WEST in legal:
                moves.append(ActionType.WEST)

        return moves[0] if moves else random.choice(legal)

    def _get_best_move_away_from(self, current: Position, threat: Position) -> ActionType:
        dx = current.x - threat.x
        dy = current.y - threat.y
        legal = self.world.get_legal_actions(current)

        moves = []
        if abs(dx) >= abs(dy):
            if dx > 0 and ActionType.EAST in legal:
                moves.append(ActionType.EAST)
            elif dx < 0 and ActionType.WEST in legal:
                moves.append(ActionType.WEST)
        else:
            if dy > 0 and ActionType.SOUTH in legal:
                moves.append(ActionType.SOUTH)
            elif dy < 0 and ActionType.NORTH in legal:
                moves.append(ActionType.NORTH)

        return moves[0] if moves else random.choice(legal)

    def execute_simulation_step(self) -> int:
        self.step_logical_clock()

        entities = self.entity_manager.get_alive_entities()
        # Cats act first, then mice, with internal shuffle
        cats = [e for e in entities if e.type == EntityType.CAT and e.alive]
        mice = [e for e in entities if e.type == EntityType.MOUSE and e.alive]
        random.shuffle(cats)
        random.shuffle(mice)

        # 1. Cats turn
        for cat in cats:
            if not cat.alive:
                continue

            # Check directional slash AoE (front of face)
            aoe_tiles = self.world.get_slash_aoe_tiles(cat.position, cat.facing, range_tiles=2)
            mice_in_slash_range = [
                m for m in self.entity_manager.get_alive_entities()
                if m.type == EntityType.MOUSE and (m.position.x, m.position.y) in aoe_tiles
            ]

            if mice_in_slash_range:
                # Issue frontal slash attack!
                result = self.action_handler.apply_action(cat, ActionType.ATTACK, self.tick)
                self.captures += len(result.affected_entities)
                for victim_id in result.affected_entities:
                    victim = self.entity_manager.entities.get(victim_id)
                    v_pos = f"({victim.position.x}, {victim.position.y})" if victim else "?"
                    msg = f"💥 [TICK {self.tick} | R{self.round}] CAPTURE: {cat.id} at ({cat.position.x}, {cat.position.y}) facing {cat.facing.value.upper()} slashed and captured {victim_id} at {v_pos}! (Total captures: {self.captures})"
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
                continue

            # Check forward FoV (4 tiles ahead)
            fov_tiles = self.world.get_forward_fov_tiles(cat.position, cat.facing, distance=4)
            mice_in_fov = [
                m for m in self.entity_manager.get_alive_entities()
                if m.type == EntityType.MOUSE and (m.position.x, m.position.y) in fov_tiles
            ]

            if mice_in_fov:
                # Target nearest visible mouse and advance
                target_mouse = min(
                    mice_in_fov,
                    key=lambda m: (m.position.x - cat.position.x)**2 + (m.position.y - cat.position.y)**2
                )
                move = self._get_best_move_towards(cat.position, target_mouse.position)
                self.action_handler.apply_action(cat, move, self.tick)
            else:
                # Stalk / wander: bias towards continuing facing direction
                f_delta = self.world.get_forward_delta(cat.facing)
                ahead_pos = (cat.position.x + f_delta[0], cat.position.y + f_delta[1])
                legal = self.world.get_legal_actions(cat.position)

                # 60% chance to keep moving forward if legal
                if random.random() < 0.6 and self.world.is_in_bounds(*ahead_pos):
                    act = None
                    if cat.facing == Direction.NORTH and ActionType.NORTH in legal:
                        act = ActionType.NORTH
                    elif cat.facing == Direction.SOUTH and ActionType.SOUTH in legal:
                        act = ActionType.SOUTH
                    elif cat.facing == Direction.EAST and ActionType.EAST in legal:
                        act = ActionType.EAST
                    elif cat.facing == Direction.WEST and ActionType.WEST in legal:
                        act = ActionType.WEST
                    if act:
                        self.action_handler.apply_action(cat, act, self.tick)
                        continue

                chosen = random.choice(legal)
                self.action_handler.apply_action(cat, chosen, self.tick)

        # 2. Mice turn
        for mouse in mice:
            if not mouse.alive:
                continue

            # Check if any Cat is close (<= 3 tiles)
            nearby_cats = [
                c for c in self.entity_manager.get_alive_entities()
                if c.type == EntityType.CAT and abs(c.position.x - mouse.position.x) <= 3 and abs(c.position.y - mouse.position.y) <= 3
            ]

            if nearby_cats:
                threat = nearby_cats[0]
                flee_move = self._get_best_move_away_from(mouse.position, threat.position)
                self.action_handler.apply_action(mouse, flee_move, self.tick)
            else:
                legal = self.world.get_legal_actions(mouse.position)
                chosen = random.choice(legal)
                self.action_handler.apply_action(mouse, chosen, self.tick)

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
