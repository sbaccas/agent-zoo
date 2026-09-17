from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Observation(BaseModel):
    tick: int
    round: int
    self: Dict[str, Any]
    visible_entities: List[Dict[str, Any]] = []
    legal_actions: List[str] = []
    capabilities: Optional[Dict[str, Any]] = None
    # Cumulative captures for this cat, and the full per-cat leaderboard. These are
    # raw counters: the agent infers whether an attack landed by diffing `score`
    # against the previous tick, rather than being told "hit" or "miss".
    score: int = 0
    scoreboard: Dict[str, int] = Field(default_factory=dict)


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
    """The cat's per-tick answer sheet, filled in top to bottom.

    Field order is load-bearing. Structured output is generated one token at a
    time in declaration order, and the actor runs with thinking_budget=0, so
    there is no scratchpad anywhere else. Whichever field is declared first is
    decided with nothing in context.

    ``thought`` therefore precedes ``action`` so the model writes its geometry
    before choosing, and selects the action while conditioned on the numbers it
    just produced.

    This used to be the other way round. Measured over 211 recorded attacks,
    the cat's forward/offaxis arithmetic was correct 94.7% of the time, yet
    only 19% of its swings had a mouse in the strike zone: it named the action
    before computing anything, so ``thought`` became a post-hoc justification
    it could not retract. Transcripts show the contradiction plainly, e.g.
    tick 134 - "A mouse is in the strike zone; attacking to score. forward: 8,
    offaxis: 2. Not in strike zone, repositioning for a better shot." - while
    ``action`` said attack. Re-running one fixed position 10 times per schema,
    action-first chose an out-of-range attack 10/10; thought-first, 1/10.
    """

    thought: str = Field(description="Concise tactical reasoning based on game skills and rules")
    action: str = Field(description="The chosen action strictly from legal_actions")
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

