from enum import Enum
from typing import Any, Dict, List, Optional
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
    cooldown: int = 0
    details: Optional[Dict[str, Any]] = None


class ObservationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    simulation_id: str
    tick: int
    round: int
    self_entity: Entity = Field(..., alias="self", serialization_alias="self")
    visible_entities: List[Entity]
    legal_actions: List[ActionType]
    capabilities: Optional[Dict[str, Any]] = None
    # Cumulative captures for this agent, and for every cat on the board. Raw
    # counters only -- the agent infers hit/miss by diffing across ticks.
    score: int = 0
    scoreboard: Dict[str, int] = Field(default_factory=dict)
    deaths: List[Dict[str, Any]] = Field(default_factory=list)
    recent_deaths: List[Dict[str, Any]] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)



class RegisterAgentRequest(BaseModel):
    agent_url: Optional[str] = None
    mode: str = "auto"  # "auto", "manual", "http", "ws"
