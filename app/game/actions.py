from app.game.world import World
from app.models.action import ActionRequest, ActionResult, ActionType
from app.models.entity import Entity, Position


class ActionHandler:
    def __init__(self, world: World):
        self.world = world

    def apply_action(self, entity: Entity, action: ActionType, current_tick: int) -> ActionResult:
        prev_pos = Position(x=entity.position.x, y=entity.position.y)
        legal_actions = self.world.get_legal_actions(prev_pos)

        if action not in legal_actions:
            return ActionResult(
                accepted=False,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                message=f"Illegal action: {action.value} from ({prev_pos.x}, {prev_pos.y})",
            )

        new_pos = self.world.get_next_position(prev_pos, action)
        entity.position = new_pos
        return ActionResult(
            accepted=True,
            tick=current_tick,
            previous_position=prev_pos,
            new_position=new_pos,
            message="Action applied successfully",
        )
