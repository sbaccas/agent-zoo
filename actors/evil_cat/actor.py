"""Evil Cat Tactical Fast Brain (actor.py).

Responsible purely for tick-by-tick tactical execution on the grid:
- Completely self-contained (zero imports from `actors.cat`).
- No chatting or broadcasting tools (zero network tool latency overhead).
- Lean prompt focusing on immediate perception and the Coach's standing directive.
- Executes the Coach's active Mode, Trigger, and Behavior.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.sessions import InMemorySessionService
from google.genai import types

from actors.evil_cat import store
from actors.evil_cat.vision import (
    format_cardinal_target_legend,
    render_viewport_data_uri,
    render_viewport_png,
)
from actors.models import CatTacticalDecision, Observation, StandingDirective

logger = logging.getLogger("evil_cat_actor")

# Model configuration
DEFAULT_ACTOR_MODEL = "gemini-2.5-flash"
ACTOR_MODEL = os.environ.get("CAT_MODEL", DEFAULT_ACTOR_MODEL)

SKILLS_PATH = Path(__file__).resolve().parent / "skills.md"

THINKING_BUDGET_OFF = 0
THINKING_BUDGET_AUTO = -1
THINKING_BUDGET_MIN = 0
THINKING_BUDGET_MAX = 24576
THINKING_BUDGET_CAN_BE_DISABLED = True
THINKING_CAN_BE_DISABLED = True
THINKING_BUDGET_DEFAULT = 128
DEFAULT_THINKING_BUDGET_ENV = "CAT_THINKING_BUDGET"
COMMANDER_FRESH_TICKS = 10

DELIBERATION_TIMEOUT_DEFAULT_SEC = 8.0
DELIBERATION_TIMEOUT_MIN_SEC = 1.0
DELIBERATION_TIMEOUT_MAX_SEC = 120.0
DELIBERATION_TIMEOUT_SECONDS = DELIBERATION_TIMEOUT_DEFAULT_SEC

_OUTPUT_TOKENS_PER_SEC = 150.0
_BASELINE_LATENCY_SEC = 1.3
_AUTO_TYPICAL_CEILING = 8192

_MODEL_THINKING_BOUNDS: Dict[str, Tuple[int, int, bool]] = {
    "gemini-2.5-flash": (0, 24576, True),
    "gemini-2.5-flash-lite": (512, 24576, True),
    "gemini-2.5-pro": (128, 32768, False),
}


def thinking_bounds_for(model: str) -> Tuple[int, int, bool]:
    """Returns `(min_tokens, max_tokens, can_be_disabled)` for `model`."""
    for prefix, bounds in sorted(
        _MODEL_THINKING_BOUNDS.items(), key=lambda kv: -len(kv[0])
    ):
        if model.startswith(prefix):
            return bounds
    return (0, 24576, True)


def resolve_skills_path(agent_id: Optional[str] = None) -> Path:
    custom = os.environ.get("CAT_SKILLS_PATH")
    if custom:
        p = Path(custom)
        if not p.is_absolute():
            p = (Path(__file__).resolve().parents[2] / p).resolve()
        if p.exists():
            return p
    return SKILLS_PATH


def _initial_thinking_budget() -> int:
    raw = os.environ.get(DEFAULT_THINKING_BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return THINKING_BUDGET_DEFAULT
    try:
        return coerce_thinking_budget(raw)
    except ValueError:
        return THINKING_BUDGET_DEFAULT


def coerce_thinking_budget(value: Any, model: Optional[str] = None) -> int:
    try:
        val = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"thinking budget must be an integer, got {value!r}")
    if val == THINKING_BUDGET_AUTO:
        return val
    if val < 0 or val > THINKING_BUDGET_MAX:
        raise ValueError(f"thinking budget {val} is outside allowed range 0..{THINKING_BUDGET_MAX}")
    return val


def describe_thinking_budget(budget: Optional[int]) -> str:
    if budget is None or budget == THINKING_BUDGET_OFF:
        return "off (0 tokens)"
    if budget == THINKING_BUDGET_AUTO:
        return "auto (model decides)"
    return f"{budget:,} tokens"


def summarize_agent(agent: LlmAgent) -> Dict[str, Any]:
    budget: Optional[int] = None
    cfg = getattr(agent, "generate_content_config", None)
    if cfg and getattr(cfg, "thinking_config", None):
        budget = cfg.thinking_config.thinking_budget
    return {
        "name": agent.name,
        "model": str(agent.model),
        "thinking_budget": budget,
        "thinking_budget_label": describe_thinking_budget(budget),
    }


def get_skills_instruction(agent_id: Optional[str] = None) -> str:
    path = resolve_skills_path(agent_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Evil Cat skills manual missing at {path}."
        )
    return path.read_text(encoding="utf-8")


def _actor_instruction_provider(_ctx: ReadonlyContext) -> str:
    return get_skills_instruction("cat-3")


def get_thinking_budget() -> int:
    try:
        cfg = getattr(adk_agent, "generate_content_config", None)
        if cfg and getattr(cfg, "thinking_config", None):
            val = cfg.thinking_config.thinking_budget
            return int(val) if val is not None else 0
    except Exception:
        pass
    return THINKING_BUDGET_DEFAULT


def set_thinking_budget(value: Any) -> int:
    budget = coerce_thinking_budget(value)
    cfg = getattr(adk_agent, "generate_content_config", None)
    if cfg is None:
        cfg = types.GenerateContentConfig()
        adk_agent.generate_content_config = cfg
    cfg.thinking_config = types.ThinkingConfig(thinking_budget=budget)
    return budget


_deliberation_timeout_sec: float = DELIBERATION_TIMEOUT_DEFAULT_SEC


def coerce_deliberation_timeout(value: Any) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"deliberation timeout must be a float, got {value!r}")
    if val < DELIBERATION_TIMEOUT_MIN_SEC or val > DELIBERATION_TIMEOUT_MAX_SEC:
        raise ValueError(
            f"deliberation timeout {val} is outside allowed range "
            f"{DELIBERATION_TIMEOUT_MIN_SEC}..{DELIBERATION_TIMEOUT_MAX_SEC}"
        )
    return val


def get_deliberation_timeout() -> float:
    return _deliberation_timeout_sec


def set_deliberation_timeout(value: Any) -> float:
    global _deliberation_timeout_sec
    _deliberation_timeout_sec = coerce_deliberation_timeout(value)
    return _deliberation_timeout_sec


def estimate_affordable_tokens(timeout_sec: float, baseline_latency_sec: float) -> int:
    return int(max(0.0, timeout_sec - baseline_latency_sec) * _OUTPUT_TOKENS_PER_SEC)


def estimate_required_timeout(
    tokens: int, baseline_latency_sec: float, max_timeout_sec: Optional[float] = None
) -> float:
    needed = baseline_latency_sec + (tokens / _OUTPUT_TOKENS_PER_SEC)
    if max_timeout_sec is None:
        return float(math.ceil(needed))
    return float(min(max_timeout_sec, math.ceil(needed)))


def nominal_thinking_tokens(budget: Optional[int]) -> Optional[int]:
    if budget is None or budget == THINKING_BUDGET_OFF:
        return None
    return _AUTO_TYPICAL_CEILING if budget == THINKING_BUDGET_AUTO else budget


def affordable_thinking_tokens(timeout_sec: Optional[float] = None) -> int:
    if timeout_sec is None:
        timeout_sec = get_deliberation_timeout()
    return estimate_affordable_tokens(timeout_sec, _BASELINE_LATENCY_SEC)


def suggested_deliberation_timeout(budget: Optional[int]) -> Optional[float]:
    tokens = nominal_thinking_tokens(budget)
    if tokens is None or tokens <= affordable_thinking_tokens():
        return None
    return estimate_required_timeout(
        tokens, _BASELINE_LATENCY_SEC, DELIBERATION_TIMEOUT_MAX_SEC
    )


def thinking_budget_warning(budget: Optional[int]) -> Optional[str]:
    if budget is None or budget == THINKING_BUDGET_OFF:
        return None
    timeout_sec = get_deliberation_timeout()
    affordable = affordable_thinking_tokens(timeout_sec)
    suggested = suggested_deliberation_timeout(budget)
    if suggested is None:
        return None
    subject = (
        f"Auto can spend up to ~{_AUTO_TYPICAL_CEILING:,} thinking tokens, which"
        if budget == THINKING_BUDGET_AUTO
        else f"{budget:,} thinking tokens is"
    )
    return (
        f"{subject} unlikely to fit inside the {timeout_sec:g}s deliberation "
        f"timeout (~{affordable:,} tokens of headroom at current latency). "
        f"Raise the timeout to about {suggested:g}s to give this budget room to land."
    )


_auth_cache: Dict[str, Any] = {
    "timestamp": 0.0,
    "result": None,
    "credentials": None,
    "detected_project": None,
}


def is_auth_error(error_msg: str) -> bool:
    if not error_msg:
        return False
    low = error_msg.lower()
    return any(
        kw in low
        for kw in (
            "unauthenticated",
            "invalid authentication",
            "credentials",
            "reauthentication",
            "refresherror",
            "defaultcredentialserror",
            "application default credentials",
            "api_key_invalid",
            "api key not valid",
            "access token",
            "401",
            "403",
        )
    )


def mark_auth_error(error_msg: str) -> bool:
    if not is_auth_error(error_msg):
        return False
    _auth_cache["timestamp"] = time.time()
    _auth_cache["credentials"] = None
    _auth_cache["detected_project"] = None
    _auth_cache["result"] = {
        "authenticated": False,
        "mode": "error",
        "error": error_msg,
    }
    return True


def clear_auth_error() -> None:
    if _auth_cache.get("result") and _auth_cache["result"].get("mode") == "error":
        _auth_cache["timestamp"] = 0.0
        _auth_cache["result"] = None


def check_gemini_auth_health(force: bool = False, force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    do_force = bool(force or force_refresh)
    if not do_force and _auth_cache["result"] is not None and (now - _auth_cache["timestamp"] < 5.0):
        return _auth_cache["result"]

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if api_key and api_key.strip():
        res = {"authenticated": True, "mode": "api_key", "status": "ok", "error": None}
        _auth_cache["timestamp"] = now
        _auth_cache["result"] = res
        return res

    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1", "yes")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if use_vertex or project:
        try:
            import google.auth
            from google.auth.transport.requests import Request

            credentials = None if do_force else _auth_cache.get("credentials")
            detected_project = _auth_cache.get("detected_project")
            loaded_fresh = False
            if credentials is None:
                credentials, detected_project = google.auth.default()
                loaded_fresh = True

            if not credentials.valid:
                try:
                    credentials.refresh(Request())
                except Exception:
                    if not loaded_fresh:
                        credentials, detected_project = google.auth.default()
                        if not credentials.valid:
                            credentials.refresh(Request())
                    else:
                        raise

            _auth_cache["credentials"] = credentials
            _auth_cache["detected_project"] = detected_project
            res = {
                "authenticated": True,
                "mode": "vertex_ai",
                "status": "ok",
                "project": project or detected_project,
                "error": None,
            }
            _auth_cache["timestamp"] = now
            _auth_cache["result"] = res
            return res
        except Exception as exc:
            _auth_cache["credentials"] = None
            _auth_cache["detected_project"] = None
            res = {
                "authenticated": False,
                "mode": "vertex_ai",
                "status": "error",
                "error": f"Vertex AI ADC expired or invalid: {exc}",
            }
            _auth_cache["timestamp"] = now
            _auth_cache["result"] = res
            return res

    res = {
        "authenticated": False,
        "mode": "none",
        "status": "unconfigured",
        "error": "No GOOGLE_API_KEY or Vertex AI configuration found",
    }
    _auth_cache["timestamp"] = now
    _auth_cache["result"] = res
    return res


def has_gemini_credentials() -> bool:
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return True
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1", "yes"):
        return True
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return True
    return False


def get_gemini_auth_mode() -> str:
    return check_gemini_auth_health().get("mode", "none")


def is_gemini_authenticated() -> bool:
    return bool(check_gemini_auth_health().get("authenticated", False))


# Initialize ADK Agent and Runner (No tools!)
adk_agent = LlmAgent(
    name="evil_cat_tactician",
    model=ACTOR_MODEL,
    instruction=_actor_instruction_provider,
    tools=[],
    output_schema=CatTacticalDecision,
    output_key="decision",
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_budget=_initial_thinking_budget()
        )
    ),
)
session_service = InMemorySessionService()
adk_runner = Runner(
    agent=adk_agent,
    app_name="evil_cat_agent_app",
    session_service=session_service,
)


async def reset_adk_session(agent_id: str = "cat-3") -> None:
    user_id = f"evil_actor_{agent_id}"
    session_id = f"session_evil_actor_{agent_id}"
    try:
        await session_service.delete_session(
            app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id
        )
    except Exception:
        pass


def format_events(events: List[Dict[str, Any]]) -> str:
    if not events:
        return ""
    lines = []
    for ev in events[-6:]:
        tick = ev.get("tick")
        tick_str = f" [Tick {tick}]" if tick is not None else ""
        ev_type = ev.get("type", "event")
        if ev_type == "hit":
            target = ev.get("target_id", "entity")
            dmg = ev.get("damage", 34)
            lines.append(f"  ⚡ HIT LANDED{tick_str}: Slashed {target} for {dmg} damage!")
        elif ev_type == "eliminated":
            killer = ev.get("by", "rival")
            lines.append(f"  ☠️ FALLEN IN BATTLE{tick_str}: Eliminated by {killer}!")
    return "Arena Combat Events:\n" + "\n".join(lines) + "\n\n" if lines else ""


def build_decision_prompt(
    obs: Observation,
    history: Optional[List[Dict[str, Any]]] = None,
    agent_id: str = "cat-3",
    history_window_size: int = 25,
    capture: Optional[Dict[str, Any]] = None,
    commander_directive: Optional[Dict[str, Any]] = None,
) -> str:
    """Builds lean, laser-focused decision prompt for Evil Cat."""
    pos = obs.self.get("position", {})
    facing = obs.self.get("facing", "north")
    entities = obs.visible_entities
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]

    # History slice (short horizon)
    window_size = max(1, history_window_size)
    recent_slice = history[-window_size:] if history else []
    history_lines = []
    for h in recent_slice:
        h_obs = h.get("observation", {}) if isinstance(h.get("observation"), dict) else {}
        h_tick = h.get("tick", h_obs.get("tick", "?"))
        h_self = h_obs.get("self", {}) if isinstance(h_obs.get("self"), dict) else {}
        h_pos = h_self.get("position") or {"x": h.get("x", 0), "y": h.get("y", 0)}
        h_facing = h_self.get("facing") or h.get("facing", "north")
        h_score = h_obs.get("score", h.get("score", 0))
        h_resp = h.get("response") if isinstance(h.get("response"), dict) else {}
        raw_act = h_resp.get("action") or h.get("action") or "stay"
        if isinstance(raw_act, dict):
            raw_act = raw_act.get("direction") or raw_act.get("action") or "stay"
        h_act = str(raw_act).upper()
        h_th = str(h_resp.get("thought") or h.get("thought") or "")
        h_cd = int(h_self.get("action_cooldown", h.get("action_cooldown", 0)) or 0)
        h_can_act = h_self.get("can_act")
        if h.get("tick_type") == "cooldown" or h.get("executed") is False:
            h_locked = True
        else:
            h_locked = (h_cd > 0) if h_can_act is None else (not h_can_act)
        outcome = "REJECTED - you were locked" if h_locked else "executed"
        rp = h_resp.get("resulting_posture") if isinstance(h_resp, dict) else None
        if rp and rp.get("position"):
            rp_pos = rp.get("position", {})
            rp_facing = (rp.get("facing") or h_facing).upper()
            posture_str = f"({h_pos.get('x')}, {h_pos.get('y')}) [{h_facing.upper()}] ➔ ({rp_pos.get('x')}, {rp_pos.get('y')}) [{rp_facing}]"
        else:
            posture_str = f"Pos ({h_pos.get('x')}, {h_pos.get('y')}) facing {h_facing.upper()}"
        h_vis = h_obs.get("visible_entities") or h.get("visible") or []
        if h_vis:
            vis_parts = []
            for v in h_vis:
                vid = v.get("id", "entity")
                vpos = v.get("position") if isinstance(v.get("position"), dict) else {"x": v.get("x"), "y": v.get("y")}
                vis_parts.append(f"{vid}@({vpos.get('x')},{vpos.get('y')})")
            vis_str = f" | Saw: {', '.join(vis_parts)}"
        else:
            vis_str = " | Saw: nothing"
        history_lines.append(
            f"- Tick {h_tick}: {posture_str} | Score: {h_score}{vis_str} | Move: {h_act} [{outcome}] | Thought: {h_th}"
        )
    history_context = ""
    if history_lines:
        history_context = "--- RECENT TACTICAL HISTORY ---\n" + "\n".join(history_lines) + "\n\n"

    # Evaluator notes / Coach Standing Directive (validated via StandingDirective model)
    standing_directive = store.read_standing_directive(agent_id=agent_id)
    if standing_directive is not None:
        directive_body = standing_directive.to_block()
    else:
        coaching_notes = store.read_coaching_notes(agent_id=agent_id)
        directive_body = coaching_notes.strip() if (coaching_notes and coaching_notes.strip()) else "(None)"

    coaching_context = (
        f"--- EVALUATOR NOTES (PRIMARY OBJECTIVE: STANDING DIRECTIVE) ---\n"
        f"{directive_body}\n\n"
    )

    # Notice board
    notice_board = store.read_notice_board()
    notice_context = ""
    if notice_board:
        notice_context = f"--- GLOBAL ARENA NOTICE BOARD ---\n{notice_board}\n\n"

    # Observation & Live Capabilities (matching actors/cat/actor.py)
    caps = obs.capabilities or {}
    atk_range = caps.get("attack_range", 2)
    vision_rad = caps.get("vision_radius", 4)
    vision_rear = caps.get("vision_rear", 2)
    vision_half = caps.get("vision_half_width", 3)
    move_cd = caps.get("move_cooldown", 3)
    atk_cd = caps.get("attack_cooldown", 3)
    lunge_cd = caps.get("lunge_cooldown", 2)

    self_hp = obs.self.get("hp", 150)
    self_max_hp = obs.self.get("max_hp", 150)
    self_atk_power = caps.get("attack_power", 34)
    self_alive = obs.self.get("alive", True)

    self_cd = int(obs.self.get("action_cooldown", 0) or 0)
    self_locked_action = obs.self.get("locked_action")
    _can_act = obs.self.get("can_act")
    self_locked = (self_cd > 0) if _can_act is None else (not _can_act)
    if not self_alive or self_hp <= 0:
        lock_text = "ELIMINATED / DEAD. You have been knocked out and cannot act."
    elif self_locked:
        recovering = f" from '{self_locked_action}'" if self_locked_action else ""
        remaining = (
            f" You are free again in {self_cd} tick(s)."
            if self_cd > 0
            else " This is the last tick of the lock; you are free next tick."
        )
        lock_text = (
            f"LOCKED. You are still recovering{recovering} and cannot act this tick."
            f"{remaining}\n"
            f"  No action can be executed on this tick.\n"
            f"  What you return is recorded as your standing intent: it is what will\n"
            f"  be applied the moment your lock clears. Answer the question \"what do\n"
            f"  I do the instant I am free?\", using what you can see right now."
        )
    else:
        lock_text = (
            "FREE. Your lock has cleared and you are acting this tick.\n"
            "  What you return becomes your standing intent for your next opportunity."
        )

    aim_line = ""
    if caps.get("aim_assist"):
        aim_line = (
            f"  • aim_assist: ON. Each visible entity below carries forward, offaxis\n"
            f"    and in_strike_zone, computed by the engine in your frame. forward is\n"
            f"    tiles ahead of you (negative = behind); offaxis is tiles to your side,\n"
            f"    either sign. in_strike_zone is true exactly when\n"
            f"    1 <= forward <= {atk_range} AND |offaxis| <= 1, both inclusive -- the zone\n"
            f"    is 3 tiles wide, so offaxis of -1 or +1 is a legal hit, not a miss.\n"
            f"    These are the engine's own numbers. Trust them over any distance you\n"
            f"    work out yourself, and do not re-derive them: if in_strike_zone is\n"
            f"    true, ATTACK connects; if it is false, ATTACK cannot connect.\n"
        )

    absorption_line = ""
    if caps.get("absorption"):
        absorption_line = (
            "  • kill_absorption: ACTIVE. Eliminating a rival predator transfers their entire kill score to you.\n"
        )

    # Rivals and bounty status
    rivals = {k: v for k, v in (obs.scoreboard or {}).items() if k != agent_id}
    eliminated = set(getattr(obs, "eliminated_cats", None) or [])
    rival_entries = []
    for cid, sc in sorted(rivals.items()):
        bounty = sc
        status = "[DEAD]" if cid in eliminated else f"[ALIVE - Bounty: +{bounty} pts]"
        rival_entries.append(f"{cid}: {sc} score {status}")
    rival_text = ", ".join(rival_entries) if rival_entries else "none"

    target_legend = format_cardinal_target_legend(obs, agent_id=agent_id)
    events_heraldry = format_event_heraldry(obs.events, obs.tick) if obs.events else ""

    observation_context = (
        f"{events_heraldry}"
        f"--- CURRENT OBSERVATION (Tick {obs.tick}, Round {obs.round}) ---\n"
        f"Agent ID: {obs.self.get('id', agent_id)} (Evil Cat | 150 HP Tank)\n"
        f"Action State: {lock_text}\n"
        f"Current Position: (x={pos.get('x', 0)}, y={pos.get('y', 0)})\n"
        f"Current Facing: {facing}\n"
        f"Health: {self_hp}/{self_max_hp} HP\n"
        f"Your Score: {obs.score}\n"
        f"Rival Cats: {rival_text}\n"
        f"Capabilities this tick:\n"
        f"  • health: {self_hp}/{self_max_hp} HP\n"
        f"  • attack_power: {self_atk_power}/100 damage per slash\n"
        f"  • attack_range: {atk_range} tiles deep, strike zone 3 tiles wide\n"
        f"  • vision_radius: {vision_rad} tiles ahead, plus {vision_rear} behind and "
        f"{vision_half} either side (a box around you, {2 * vision_half + 1} tiles wide)\n"
        f"  • move_cooldown: {move_cd} tick(s)\n"
        f"  • attack_cooldown: {atk_cd} tick(s)\n"
        f"  • lunge_cooldown: {lunge_cd} tick(s)\n"
        f"{aim_line}"
        f"{absorption_line}"
        f"2D Viewport Image: Attached as multimodal image/png\n"
        f"{target_legend}\n"
        f"Visible Entities in Viewport: {entities}\n"
        f"Legal Actions this tick: {legal}"
    )

    prompt = f"{history_context}{notice_context}{coaching_context}{observation_context}"

    if capture is not None:
        viewport_png_bytes, viewport_data_uri = render_viewport_data_uri(obs, agent_id=agent_id)
        capture.update({
            "built_at_tick": obs.tick,
            "model": ACTOR_MODEL,
            "thinking_budget": get_thinking_budget(),
            "multimodal_viewport_bytes": len(viewport_png_bytes),
            "sections": {
                "skills": {"label": "Evil Cat Skills", "delivered": True, "text": get_skills_instruction(agent_id)},
                "history": {"label": "Recent History", "delivered": bool(history_context), "text": history_context},
                "standing_order": {"label": "Coach Standing Directive (PRIMARY OBJECTIVE)", "delivered": True, "text": coaching_context},
                "viewport_image": {
                    "label": "Multimodal 2D Viewport Cutout (image/png -> Gemini Vision)",
                    "delivered": True,
                    "text": (
                        f"Attached North-Up 2D Viewport PNG ({len(viewport_png_bytes)} bytes, ~258 vision tokens)\n"
                        f"{target_legend}"
                    ),
                    "image_data_uri": viewport_data_uri,
                },
                "observation": {"label": "Current Observation", "delivered": True, "text": observation_context},
            },
            "full_prompt": prompt,
        })

    return prompt


from app.otel.decorators import record_genai_metrics, traced_genai


@traced_genai(role="actor", model=ACTOR_MODEL, span_name="genai.actor_deliberation")
async def query_adk_gemini(
    obs: Observation,
    history: Optional[List[Dict[str, Any]]] = None,
    agent_id: str = "cat-3",
    history_window_size: int = 25,
    capture: Optional[Dict[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
    commander_directive: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[CatTacticalDecision], str, Optional[str]]:
    """Runs one tactical deliberation turn with Google ADK Gemini (multimodal 2D viewport + text)."""
    started = time.monotonic()
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace import SpanKind as _SpanKind, Status as _Status, StatusCode as _StatusCode
    _tracer = _otel_trace.get_tracer("cm.genai")

    with _tracer.start_as_current_span(
        "actor.prepare_prompt",
        kind=_SpanKind.INTERNAL,
        attributes={"cm.agent_id": agent_id, "cm.tick": int(obs.tick)},
    ) as _prep_span:
        prompt = build_decision_prompt(
            obs=obs,
            history=history,
            agent_id=agent_id,
            history_window_size=history_window_size,
            capture=capture,
            commander_directive=commander_directive,
        )
        _prep_span.set_attribute("cm.prompt_chars", len(prompt))
        _prep_span.set_status(_Status(_StatusCode.OK))

    if not has_gemini_credentials():
        return None, "heuristic_fallback", "no Gemini credentials configured"

    user_id = f"evil_actor_{agent_id}"
    session_id = f"session_evil_actor_{agent_id}"
    ttft_ms: Optional[float] = None
    input_tokens = 0
    output_tokens = 0
    thoughts_tokens: Optional[int] = None

    try:
        with _tracer.start_as_current_span(
            "actor.render_viewport_png",
            kind=_SpanKind.INTERNAL,
            attributes={"cm.agent_id": agent_id, "cm.tick": int(obs.tick)},
        ) as _render_span:
            try:
                await session_service.delete_session(app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id)
            except Exception:
                pass
            await session_service.create_session(app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id)

            viewport_png = render_viewport_png(obs, agent_id=agent_id)
            _render_span.set_attribute("cm.viewport_png_bytes", len(viewport_png or b""))
            _render_span.set_status(_Status(_StatusCode.OK))

        msg = types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=viewport_png, mime_type="image/png"),
                types.Part.from_text(text=prompt),
            ],
        )
        effective_timeout = timeout_seconds or get_deliberation_timeout()

        async def _run_stream() -> Tuple[Optional[CatTacticalDecision], Optional[int]]:
            nonlocal ttft_ms, input_tokens, output_tokens, thoughts_tokens
            final_decision: Optional[CatTacticalDecision] = None
            agen = adk_runner.run_async(user_id=user_id, session_id=session_id, new_message=msg)
            try:
                while True:
                    ev = await agen.__anext__()
                    if ttft_ms is None:
                        ttft_ms = round((time.monotonic() - started) * 1000.0, 2)
                    if ev.error_code or ev.error_message:
                        logger.warning("Evil Cat ADK Error: %s - %s", ev.error_code, ev.error_message)
                    usage = getattr(ev, "usage_metadata", None)
                    if usage:
                        p_tok = getattr(usage, "prompt_token_count", None)
                        c_tok = getattr(usage, "candidates_token_count", None)
                        spent = getattr(usage, "thoughts_token_count", None)
                        if p_tok is not None:
                            input_tokens = max(input_tokens, int(p_tok))
                        if c_tok is not None:
                            output_tokens = max(output_tokens, int(c_tok))
                        if spent is not None:
                            thoughts_tokens = (thoughts_tokens or 0) + int(spent)
                    if ev.actions and ev.actions.state_delta and "decision" in ev.actions.state_delta:
                        final_decision = ev.actions.state_delta["decision"]
                    elif ev.content and ev.content.parts:
                        for p in ev.content.parts:
                            if p.text and not getattr(p, "thought", False):
                                try:
                                    final_decision = CatTacticalDecision.model_validate_json(p.text)
                                except Exception:
                                    pass
            except StopAsyncIteration:
                pass

            if isinstance(final_decision, dict):
                try:
                    final_decision = CatTacticalDecision(**final_decision)
                except Exception:
                    pass
            return final_decision, thoughts_tokens

        decision, thoughts_tokens = await asyncio.wait_for(_run_stream(), timeout=effective_timeout)
        lat_ms = round((time.monotonic() - started) * 1000.0, 2)

        if capture is not None:
            capture["latency_ms"] = round(lat_ms)
            capture["ttft_ms"] = ttft_ms
            capture["thoughts_token_count"] = thoughts_tokens

        if decision is not None:
            clear_auth_error()
            record_genai_metrics(
                role="actor",
                model=ACTOR_MODEL,
                latency_ms=lat_ms,
                ttft_ms=ttft_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_tokens=thoughts_tokens or 0,
                status="OK",
                decision_source="adk_gemini",
            )
            return decision, "adk_gemini", None

    except asyncio.TimeoutError:
        lat_ms = round((time.monotonic() - started) * 1000.0, 2)
        reason = f"Evil Cat deliberation timed out after {timeout_seconds or get_deliberation_timeout()}s"
        logger.warning(reason)
        record_genai_metrics(
            role="actor",
            model=ACTOR_MODEL,
            latency_ms=lat_ms,
            ttft_ms=ttft_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thoughts_tokens or 0,
            status="ERROR",
            decision_source="heuristic_fallback",
            error_type="timeout",
        )
        return None, "heuristic_fallback", reason
    except Exception as exc:
        lat_ms = round((time.monotonic() - started) * 1000.0, 2)
        err = str(exc)
        mark_auth_error(err)
        logger.warning("Evil Cat deliberation error: %s", err)
        record_genai_metrics(
            role="actor",
            model=ACTOR_MODEL,
            latency_ms=lat_ms,
            ttft_ms=ttft_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thoughts_tokens or 0,
            status="ERROR",
            decision_source="heuristic_fallback",
            error_type=err,
        )
        return None, "heuristic_fallback", err

    return None, "heuristic_fallback", "Gemini returned empty decision"

