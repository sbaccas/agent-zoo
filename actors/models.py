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
    deaths: List[Dict[str, Any]] = Field(default_factory=list)
    recent_deaths: List[Dict[str, Any]] = Field(default_factory=list)


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
    time in declaration order, and the actor runs with ``thinking_budget=0`` by
    default, so there is no scratchpad anywhere else. Whichever field is
    declared first is decided with nothing in context.

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

    That 10/10 vs 1/10 was measured at ``thinking_budget=0``, which is now a
    default rather than a fixed property of the actor (see
    ``actor.set_thinking_budget``). Raising the budget gives the model a real
    scratchpad and should weaken the effect this ordering compensates for -- it
    does not invalidate the ordering, but it does mean the margin cannot be
    assumed to hold at other budgets without re-measuring.
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


class ThinkingBudgetRequest(BaseModel):
    """How much private scratchpad the actor gets before it must answer.

    Deliberately carries no ``ge``/``le`` on the budget. The valid set is
    disjoint -- 0 (off), -1 (auto), or a span whose bounds depend on which model
    the actor is running -- so a pydantic range would either reject the two
    sentinels or admit the gap between them. ``actor.coerce_thinking_budget``
    owns the rule, and owns it once, against the live model.

    The timeout rides along in the same request because the two are one
    decision. A budget the deliberation ceiling cannot afford does not produce a
    slower cat, it produces a cat that quietly stops using the model; making the
    caller set them in two round trips would leave a window where exactly that
    was true.
    """

    thinking_budget: int = Field(
        description="0 disables thinking, -1 lets the model decide, or a token count"
    )
    deliberation_timeout_sec: Optional[float] = Field(
        default=None,
        description="Seconds a decision may take before it is abandoned; omit to leave unchanged",
    )


class EvalThinkingBudgetRequest(BaseModel):
    """The same dial, for the long-horizon evaluator subagent.

    A separate model rather than a reuse of ``ThinkingBudgetRequest``. The two
    agents have genuinely different ceilings -- the actor must answer inside a
    tick, the evaluator inside a minute -- so the timeout field is named for the
    thing it bounds. Sharing one model would have made a payload aimed at the
    actor silently valid against the evaluator, and the resulting A/B would have
    moved a variable nobody was testing.
    """

    thinking_budget: int = Field(
        description="0 disables thinking, -1 lets the model decide, or a token count"
    )
    eval_timeout_sec: Optional[float] = Field(
        default=None,
        description="Seconds one evaluation may run before it is abandoned; omit to leave unchanged",
    )


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


class GeneralInterrogateRequest(BaseModel):
    """A transmission on the General Radio -- the Commander's channel to the coach."""

    prompt: str = Field(description="Plain-text question or instruction for the coach")
    # Named to match the actor channel's `steer_directive` so the two radios read
    # the same way, but it points at an entirely separate slot: this one survives
    # the coach's wholesale rewrite of its dossier, the actor's survives the
    # model's overwrite of `active_directive`.
    steer_directive: bool = Field(
        default=False,
        description="Whether to also adopt this prompt as a standing order constraining future coaching",
    )


class GeneralInterrogateResponse(BaseModel):
    reply: str = Field(description="The coach's prose reply")
    engine: str = Field(default="adk_gemini", description="Engine used: adk_gemini or offline_fallback")
    tick: int = Field(default=0, description="The tick at which the prompt was answered")
    adopted_directive: Optional[str] = Field(default=None, description="Standing order adopted if steer_directive was enabled")
    notes_in_force: int = Field(default=0, description="How many directives the coach's dossier currently holds")
    error: Optional[str] = Field(default=None, description="Error message if any occurred")

