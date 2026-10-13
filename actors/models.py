from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


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
    eliminated_cats: List[str] = Field(default_factory=list)
    deaths: List[Dict[str, Any]] = Field(default_factory=list)
    recent_deaths: List[Dict[str, Any]] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)
    incoming_transmissions: List[Dict[str, Any]] = Field(default_factory=list)


class ActionResponse(BaseModel):
    action: str
    thought: str = ""
    broadcast_msg: Optional[str] = ""
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)


class OverrideRequest(BaseModel):
    action: Optional[str] = None
    mode: Optional[str] = "once"


class ApiKeyRequest(BaseModel):
    api_key: str


class ConnectServerRequest(BaseModel):
    server_url: Optional[str] = None
    cat_id: Optional[str] = None


# The cat's per-tick answer sheet, filled in top to bottom.
# Field order is load-bearing: structured output is generated one token at a
# time in declaration order, and the actor runs with thinking_budget=0 by default.
# Whichever field is declared first is decided with nothing in context.
# ``thought`` therefore precedes ``action`` so the model writes its geometry
# before choosing, and selects the action while conditioned on the numbers it
# just produced (avoids post-hoc rationalizations).
class CatTacticalDecision(BaseModel):
    """Structured decision schema for cat agent turn."""

    thought: str = Field(description="Concise tactical reasoning based on game skills and rules")
    action: str = Field(description="The chosen action strictly from legal_actions")
    target_id: Optional[str] = Field(
        default="",
        description="ID of targeted entity (mouse or rival cat) or empty string if none",
    )
    target_mouse_id: Optional[str] = Field(
        default="",
        description="Legacy alias for target_id; ID of targeted mouse or entity",
    )
    strategy: str = Field(default="ROAMING", description="Tactical phase: ATTACK, LUNGE, STALKING, or ROAMING")
    broadcast_msg: Optional[str] = ""
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _sync_targets(cls, values: Any) -> Any:
        if isinstance(values, dict):
            eff = values.get("target_id") or values.get("target_mouse_id") or ""
            values.setdefault("target_id", eff)
            values.setdefault("target_mouse_id", eff)
            if not values.get("target_id") and eff:
                values["target_id"] = eff
            if not values.get("target_mouse_id") and eff:
                values["target_mouse_id"] = eff
        return values


class StandingDirective(BaseModel):
    """Structured standing directive contract (`MODE / TRIGGER / BEHAVIOR`) from Coach to Actor."""

    mode: str = Field(
        description="Operational mode name (e.g., STALK, TRAVEL, AMBUSH, HEAL, EVADE)"
    )
    trigger: str = Field(
        description="Conditional trigger for immediate action"
    )
    behavior: str = Field(
        description="Action and waypoint execution instruction"
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_and_validate(cls, values: Any) -> Any:
        if isinstance(values, str):
            values = cls._parse_raw_string(values)
        if not isinstance(values, dict):
            raise ValueError("StandingDirective input must be a mapping or formatted directive string")

        raw_mode = str(values.get("mode") or values.get("MODE") or "").strip().strip("`*_")
        raw_trigger = str(values.get("trigger") or values.get("TRIGGER") or "").strip()
        raw_behavior = str(values.get("behavior") or values.get("BEHAVIOR") or "").strip()

        if not raw_mode:
            raise ValueError("StandingDirective requires a non-empty MODE field")
        if not raw_trigger:
            raise ValueError("StandingDirective requires a non-empty TRIGGER field")
        if not raw_behavior:
            raise ValueError("StandingDirective requires a non-empty BEHAVIOR field")

        return {
            "mode": raw_mode.upper(),
            "trigger": raw_trigger,
            "behavior": raw_behavior,
        }

    @classmethod
    def _parse_raw_string(cls, raw: str) -> Dict[str, str]:
        import json as _json
        import re as _re

        text = (raw or "").strip()
        if not text:
            raise ValueError("StandingDirective text is empty")

        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = _json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        # Strip markdown code fences if present and extract MODE / TRIGGER / BEHAVIOR lines
        cleaned_lines = [
            ln for ln in text.splitlines() if not ln.strip().startswith("```")
        ]
        cleaned = "\n".join(cleaned_lines)

        header_re = _re.compile(
            r"(?im)^[\s>*`-]*\**(MODE|TRIGGER|BEHAVIOR)\**\s*:\s*(.*)$"
        )
        matches = list(header_re.finditer(cleaned))
        if not matches:
            raise ValueError(
                "StandingDirective text must contain MODE:, TRIGGER:, and BEHAVIOR: fields"
            )

        extracted: Dict[str, str] = {}
        for idx, m in enumerate(matches):
            key = m.group(1).upper()
            first_line = m.group(2).strip()
            start_pos = m.end()
            end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(cleaned)
            rest = cleaned[start_pos:end_pos].strip()
            if rest and key != "MODE":
                # Keep only contiguous non-empty lines belonging to this field
                cont_lines = []
                for ln in rest.splitlines():
                    s = ln.strip()
                    if not s:
                        break
                    cont_lines.append(s)
                full_val = " ".join([first_line] + cont_lines).strip() if first_line else " ".join(cont_lines).strip()
            else:
                full_val = first_line
            extracted[key.lower()] = full_val

        return extracted

    @classmethod
    def from_text(cls, text: str) -> "StandingDirective":
        return cls.model_validate(text)

    def to_block(self) -> str:
        return f"MODE: {self.mode}\nTRIGGER: {self.trigger}\nBEHAVIOR: {self.behavior}"

    def __str__(self) -> str:
        return self.to_block()

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

