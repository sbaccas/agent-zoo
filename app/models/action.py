from enum import Enum
from typing import List, Optional
from pydantic import BaseModel
from app.models.entity import Position


class ActionType(str, Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    STAY = "stay"
    ATTACK = "attack"


class ActionRequest(BaseModel):
    action: ActionType


class ActionResult(BaseModel):
    accepted: bool
    tick: int
    previous_position: Position
    new_position: Position
    action: ActionType
    affected_entities: List[str] = []
    message: Optional[str] = None
