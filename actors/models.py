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
    history_window_size: int = Field(default=25, ge=1, le=50, description="Sliding window size N for history sent to LLM")


class EvalIntervalRequest(BaseModel):
    interval: int = Field(default=100, ge=1, le=10000, description="Ticks of play between long-horizon evaluations")
    enabled: Optional[bool] = Field(default=None, description="Toggle the reflexion loop on or off; omit to leave unchanged")


class InterrogateRequest(BaseModel):
    prompt: str = Field(description="Direct plain-text prompt or interrogation question from the user")
    steer_directive: bool = Field(default=False, description="Whether to also adopt this prompt as an active movement directive")


class InterrogateResponse(BaseModel):
    reply: str = Field(description="The direct answer or reasoning from the cat agent")
    engine: str = Field(default="adk_gemini", description="Engine used: adk_gemini or heuristic_fallback")
    tick: int = Field(default=0, description="The tick at which the prompt was answered")
    posture: Dict[str, Any] = Field(default_factory=dict, description="Current position and facing of the cat")
    active_strategy: str = Field(default="ROAMING", description="Active tactical phase")
    adopted_directive: Optional[str] = Field(default=None, description="Directive adopted if steer_directive was enabled")
    error: Optional[str] = Field(default=None, description="Error message if any occurred")

