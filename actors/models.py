from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Observation(BaseModel):
    tick: int
    round: int
    self: Dict[str, Any]
    visible_entities: List[Dict[str, Any]] = []
    legal_actions: List[str] = []


class ActionResponse(BaseModel):
    action: str
    thought: str = ""


class OverrideRequest(BaseModel):
    action: Optional[str] = None
    mode: Optional[str] = "once"


class ApiKeyRequest(BaseModel):
    api_key: str


class ConnectServerRequest(BaseModel):
    server_url: Optional[str] = None
    cat_id: Optional[str] = None


class CatTacticalDecision(BaseModel):
    action: str = Field(description="The chosen action strictly from legal_actions")
    thought: str = Field(description="Concise tactical reasoning based on game skills and rules")
    target_mouse_id: Optional[str] = Field(default="", description="ID of targeted mouse or empty string if none")
    strategy: str = Field(default="ROAMING", description="Tactical phase: ATTACK, LUNGE, STALKING, or ROAMING")
