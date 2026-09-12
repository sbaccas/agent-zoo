from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.entity import Entity, Position


class ActionType(str, Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    STAY = "stay"
    ATTACK = "attack"
    LUNGE = "lunge"


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


class ObservationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    simulation_id: str
    tick: int
    round: int
    self_entity: Entity = Field(..., alias="self", serialization_alias="self")
    visible_entities: List[Entity]
    legal_actions: List[ActionType]



class RegisterAgentRequest(BaseModel):
    agent_url: Optional[str] = None
    mode: str = "auto"  # "auto", "manual", "http", "ws"
