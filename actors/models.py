from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Observation(BaseModel):
    tick: int
    round: int
    self: Dict[str, Any]
    visible_entities: List[Dict[str, Any]] = []
    legal_actions: List[str] = []
    capabilities: Optional[Dict[str, Any]] = None


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


class HistoryWindowRequest(BaseModel):
    history_window_size: int = Field(default=3, ge=1, le=50, description="Sliding window size N for history sent to LLM")

