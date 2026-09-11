from typing import List
from app.models.action import ActionType
from app.models.entity import Position
from app.models.simulation import Topology


class World:
    def __init__(self, width: int = 100, height: int = 100, topology: Topology = Topology.BOUNDED):
        self.width = width
        self.height = height
        self.topology = topology

    def is_in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get_next_position(self, pos: Position, action: ActionType) -> Position:
        dx, dy = 0, 0
        if action == ActionType.NORTH:
            dy = -1
        elif action == ActionType.SOUTH:
            dy = 1
        elif action == ActionType.EAST:
            dx = 1
        elif action == ActionType.WEST:
            dx = -1
        elif action == ActionType.STAY:
            return Position(x=pos.x, y=pos.y)

        target_x = pos.x + dx
        target_y = pos.y + dy

        if self.topology == Topology.TORUS:
            return Position(x=target_x % self.width, y=target_y % self.height)
        else:
            # Bounded: if out of bounds, clamped / illegal
            if not self.is_in_bounds(target_x, target_y):
                return Position(x=pos.x, y=pos.y)
            return Position(x=target_x, y=target_y)

    def get_legal_actions(self, pos: Position) -> List[ActionType]:
        legal = [ActionType.STAY]
        moves = [
            (ActionType.NORTH, 0, -1),
            (ActionType.SOUTH, 0, 1),
            (ActionType.EAST, 1, 0),
            (ActionType.WEST, -1, 0),
        ]
        for action, dx, dy in moves:
            nx = pos.x + dx
            ny = pos.y + dy
            if self.topology == Topology.TORUS or self.is_in_bounds(nx, ny):
                legal.append(action)
        return legal
