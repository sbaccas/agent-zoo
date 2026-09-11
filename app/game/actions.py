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

        if action == ActionType.ATTACK:
            # Execute slash attack in front of face
            aoe_tiles = self.world.get_slash_aoe_tiles(entity.position, entity.facing, range_tiles=2)
            hit_entities: List[str] = []

            target_type = EntityType.MOUSE if entity.type == EntityType.CAT else EntityType.CAT
            for tx, ty in aoe_tiles:
                occupants = self.entity_manager.get_entities_at(tx, ty)
                for victim in occupants:
                    if victim.type == target_type and victim.alive:
                        victim.alive = False
                        hit_entities.append(victim.id)

            return ActionResult(
                accepted=True,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                action=action,
                affected_entities=hit_entities,
                message=f"Attack executed hitting {len(hit_entities)} target(s)",
            )

        # Movement / Stay actions
        legal_actions = self.world.get_legal_actions(prev_pos, allow_attack=True)
        if action not in legal_actions:
            return ActionResult(
                accepted=False,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                action=action,
                message=f"Illegal action: {action.value} from ({prev_pos.x}, {prev_pos.y})",
            )

        if action in [ActionType.NORTH, ActionType.SOUTH, ActionType.EAST, ActionType.WEST]:
            entity.facing = self.world.direction_from_action(action)
            new_pos = self.world.get_next_position(prev_pos, action)
            entity.position = new_pos
        else:
            new_pos = prev_pos

        return ActionResult(
            accepted=True,
            tick=current_tick,
            previous_position=prev_pos,
            new_position=new_pos,
            action=action,
            message="Action applied successfully",
        )
