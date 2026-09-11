from enum import Enum
from typing import Optional
from pydantic import BaseModel
from app.models.entity import Position


class ActionType(str, Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    STAY = "stay"


class ActionRequest(BaseModel):
    action: ActionType


class ActionResult(BaseModel):
    accepted: bool
    tick: int
    previous_position: Position
    new_position: Position
    message: Optional[str] = None
