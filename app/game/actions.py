from typing import List
from app.game.entities import EntityManager
from app.game.world import World
from app.models.action import ActionRequest, ActionResult, ActionType
from app.models.entity import Entity, EntityType, Position


class ActionHandler:
    def __init__(self, world: World, entity_manager: EntityManager):
        self.world = world
        self.entity_manager = entity_manager

    def apply_action(self, entity: Entity, action: ActionType, current_tick: int) -> ActionResult:
        prev_pos = Position(x=entity.position.x, y=entity.position.y)

        # 1. Action lock check: Entity cannot change input while completing previous action animation
        if entity.action_cooldown > 0:
            return ActionResult(
                accepted=False,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                action=action,
                message=f"Entity {entity.id} is locked in action '{entity.locked_action}' ({entity.action_cooldown} tick(s) remaining)",
                cooldown=entity.action_cooldown,
            )

        species_key = entity.type.value
        spec_cfg = self.entity_manager.species_config.get(species_key)

        if action == ActionType.ATTACK:
            # Dynamic slash attack range based on species hyperparameter (default 2 tiles)
            range_tiles = spec_cfg.attack_range if spec_cfg and spec_cfg.attack_range > 0 else 2
            aoe_tiles = self.world.get_slash_aoe_tiles(entity.position, entity.facing, range_tiles=range_tiles)
            hit_entities: List[str] = []

            target_type = EntityType.MOUSE if entity.type == EntityType.CAT else EntityType.CAT
            for tx, ty in aoe_tiles:
                occupants = self.entity_manager.get_entities_at(tx, ty)
                for victim in occupants:
                    if victim.type == target_type and victim.alive:
                        victim.alive = False
                        hit_entities.append(victim.id)

            cooldown_dur = spec_cfg.attack_cooldown if spec_cfg else 3
            entity.action_cooldown = max(0, cooldown_dur - 1)
            entity.locked_action = action.value if entity.action_cooldown > 0 else None

            return ActionResult(
                accepted=True,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                action=action,
                affected_entities=hit_entities,
                message=f"Attack executed hitting {len(hit_entities)} target(s)",
                cooldown=entity.action_cooldown,
            )

        if action == ActionType.LUNGE:
            allow_attack = (entity.type == EntityType.CAT)
            legal_actions = self.world.get_legal_actions(prev_pos, allow_attack=allow_attack, facing=entity.facing)
            if action not in legal_actions:
                return ActionResult(
                    accepted=False,
                    tick=current_tick,
                    previous_position=prev_pos,
                    new_position=prev_pos,
                    action=action,
                    message=f"Illegal action: {action.value} from ({prev_pos.x}, {prev_pos.y}) facing {entity.facing.value}",
                    cooldown=entity.action_cooldown,
                )

            new_pos = self.world.get_next_position(prev_pos, action, facing=entity.facing)
            entity.position = new_pos

            cooldown_dur = spec_cfg.lunge_cooldown if spec_cfg else 2
            entity.action_cooldown = max(0, cooldown_dur - 1)
            entity.locked_action = action.value if entity.action_cooldown > 0 else None

            return ActionResult(
                accepted=True,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=new_pos,
                action=action,
                message="Lunge executed successfully",
                cooldown=entity.action_cooldown,
            )

        # Movement / Stay actions
        allow_attack = (entity.type == EntityType.CAT)
        legal_actions = self.world.get_legal_actions(prev_pos, allow_attack=allow_attack, facing=entity.facing)
        if action not in legal_actions:
            return ActionResult(
                accepted=False,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                action=action,
                message=f"Illegal action: {action.value} from ({prev_pos.x}, {prev_pos.y})",
                cooldown=entity.action_cooldown,
            )

        if action in [ActionType.NORTH, ActionType.SOUTH, ActionType.EAST, ActionType.WEST]:
            entity.facing = self.world.direction_from_action(action)
            new_pos = self.world.get_next_position(prev_pos, action)
            entity.position = new_pos
            cooldown_dur = spec_cfg.move_cooldown if spec_cfg else 1
        else:
            new_pos = prev_pos
            cooldown_dur = 1

        entity.action_cooldown = max(0, cooldown_dur - 1)
        entity.locked_action = action.value if entity.action_cooldown > 0 else None

        return ActionResult(
            accepted=True,
            tick=current_tick,
            previous_position=prev_pos,
            new_position=new_pos,
            action=action,
            message="Action applied successfully",
            cooldown=entity.action_cooldown,
        )
