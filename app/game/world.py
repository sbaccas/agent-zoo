from typing import List, Tuple
from app.models.action import ActionType
from app.models.entity import Direction, Position
from app.models.simulation import Topology


class World:
    def __init__(self, width: int = 100, height: int = 100, topology: Topology = Topology.BOUNDED):
        self.width = width
        self.height = height
        self.topology = topology

    def is_in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def wrap_position(self, x: int, y: int) -> Tuple[int, int]:
        if self.topology == Topology.TORUS:
            return (x % self.width, y % self.height)
        return (x, y)

    def get_forward_delta(self, facing: Direction) -> Tuple[int, int]:
        if facing == Direction.NORTH:
            return (0, -1)
        elif facing == Direction.SOUTH:
            return (0, 1)
        elif facing == Direction.EAST:
            return (1, 0)
        elif facing == Direction.WEST:
            return (-1, 0)
        return (0, 0)

    def direction_from_action(self, action: ActionType) -> Direction:
        if action == ActionType.NORTH:
            return Direction.NORTH
        elif action == ActionType.SOUTH:
            return Direction.SOUTH
        elif action == ActionType.EAST:
            return Direction.EAST
        elif action == ActionType.WEST:
            return Direction.WEST
        return Direction.NORTH

    def get_next_position(self, pos: Position, action: ActionType) -> Position:
        if action in [ActionType.STAY, ActionType.ATTACK]:
            return Position(x=pos.x, y=pos.y)

        dx, dy = 0, 0
        if action == ActionType.NORTH:
            dy = -1
        elif action == ActionType.SOUTH:
            dy = 1
        elif action == ActionType.EAST:
            dx = 1
        elif action == ActionType.WEST:
            dx = -1

        target_x = pos.x + dx
        target_y = pos.y + dy

        if self.topology == Topology.TORUS:
            wx, wy = self.wrap_position(target_x, target_y)
            return Position(x=wx, y=wy)
        else:
            if not self.is_in_bounds(target_x, target_y):
                return Position(x=pos.x, y=pos.y)
            return Position(x=target_x, y=target_y)

    def get_legal_actions(self, pos: Position, allow_attack: bool = False) -> List[ActionType]:
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

        if allow_attack:
            legal.append(ActionType.ATTACK)

        return legal

    def get_forward_fov_tiles(
        self, pos: Position, facing: Direction, distance: int = 4
    ) -> List[Tuple[int, int]]:
        """
        Returns tiles in front of the entity's face up to `distance` steps,
        including a forward-facing cone.
        """
        dx, dy = self.get_forward_delta(facing)
        # Perpendicular vector for lateral vision in forward cone
        px, py = -dy, dx

        tiles = []
        for dist in range(1, distance + 1):
            # Direct forward line
            fx = pos.x + dx * dist
            fy = pos.y + dy * dist

            # Forward cone spread: at dist 1 -> direct + 1 lateral; at dist 2..4 -> direct + spread
            lateral_spread = 1 if dist <= 2 else (dist // 2)
            for lat in range(-lateral_spread, lateral_spread + 1):
                cx = fx + px * lat
                cy = fy + py * lat
                if self.topology == Topology.TORUS:
                    tiles.append(self.wrap_position(cx, cy))
                elif self.is_in_bounds(cx, cy):
                    tiles.append((cx, cy))

        return list(set(tiles))

    def get_slash_aoe_tiles(
        self, pos: Position, facing: Direction, range_tiles: int = 2
    ) -> List[Tuple[int, int]]:
        """
        Returns Area of Effect (AoE) tiles directly in front of the entity's face.
        Cannot hit anything behind or alongside.
        """
        dx, dy = self.get_forward_delta(facing)
        px, py = -dy, dx

        tiles = []
        # 1 tile ahead + forward flanks (3-tile forward arc)
        for lat in [-1, 0, 1]:
            tx = pos.x + dx * 1 + px * lat
            ty = pos.y + dy * 1 + py * lat
            if self.topology == Topology.TORUS:
                tiles.append(self.wrap_position(tx, ty))
            elif self.is_in_bounds(tx, ty):
                tiles.append((tx, ty))

        # If range >= 2, add tile 2 steps directly ahead
        if range_tiles >= 2:
            tx2 = pos.x + dx * 2
            ty2 = pos.y + dy * 2
            if self.topology == Topology.TORUS:
                tiles.append(self.wrap_position(tx2, ty2))
            elif self.is_in_bounds(tx2, ty2):
                tiles.append((tx2, ty2))

        return list(set(tiles))
