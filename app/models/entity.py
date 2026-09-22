from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class EntityType(str, Enum):
    CAT = "cat"
    MOUSE = "mouse"


class Direction(str, Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"


class Position(BaseModel):
    x: int = Field(..., ge=0, description="X coordinate")
    y: int = Field(..., ge=0, description="Y coordinate")

    def as_tuple(self) -> tuple[int, int]:
        return (self.x, self.y)


class Entity(BaseModel):
    id: str
    type: EntityType
    position: Position
    facing: Direction = Direction.NORTH
    alive: bool = True
    weight: int = 1
    action_cooldown: int = 0
    locked_action: Optional[str] = None
    hp: int = 100
    max_hp: int = 100
