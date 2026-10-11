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
from actors.models import CatTacticalDecision, Observation

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
THINKING_BUDGET_DEFAULT = 512
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
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not read skills manual from %s: %s", path, exc)
    return "You are Evil Cat (cat-3), an apex cat-hunter. Catching mice gives 0 points but heals 50% HP. Eliminating rival cats awards 2x their score bounty. Obey your Coach's standing directive."


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
adk_interrogator_agent = LlmAgent(
    name="evil_cat_interrogator",
    model=ACTOR_MODEL,
    instruction="You are Evil Cat (cat-3), a ruthless 150 HP apex cat-hunter. Answer your Commander concisely in character.",
    tools=[],
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    ),
)
session_service = InMemorySessionService()
adk_runner = Runner(
    agent=adk_agent,
    app_name="evil_cat_agent_app",
    session_service=session_service,
)
adk_interrogator_runner = Runner(
    agent=adk_interrogator_agent,
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
    history_window_size: int = 5,
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
        h_obs = h.get("observation", {})
        h_tick = h.get("tick", "?")
        h_self = h_obs.get("self", {})
        h_pos = h_self.get("position", {})
        h_facing = h_self.get("facing", "north")
        h_score = h_obs.get("score", 0)
        h_resp = h.get("response", {})
        h_act = (h_resp.get("action") or "stay").upper()
        h_th = h_resp.get("thought", "")
        history_lines.append(
            f"- Tick {h_tick}: Pos ({h_pos.get('x')}, {h_pos.get('y')}) facing {h_facing.upper()} | Score: {h_score} | Move: {h_act} | Thought: {h_th[:75]}"
        )
    history_context = ""
    if history_lines:
        history_context = "--- RECENT TACTICAL HISTORY ---\n" + "\n".join(history_lines) + "\n\n"

    # Evaluator notes / Coach Standing Directive (PRIMARY OBJECTIVE)
    coaching_notes = store.read_coaching_notes(agent_id=agent_id)
    coaching_context = ""
    if coaching_notes and coaching_notes.strip():
        coaching_context = (
            f"--- EVALUATOR NOTES (PRIMARY OBJECTIVE: STANDING DIRECTIVE) ---\n"
            f"{coaching_notes.strip()}\n\n"
        )
    else:
        coaching_context = (
            f"--- EVALUATOR NOTES (PRIMARY OBJECTIVE: STANDING DIRECTIVE) ---\n"
            f"MODE: STALK\n"
            f"TRIGGER: rival cat in strike zone -> attack immediately\n"
            f"BEHAVIOR: Search the arena for highest bounty rival cat. Close distance and eliminate.\n\n"
        )

    # Notice board
    notice_board = store.read_notice_board()
    notice_context = ""
    if notice_board:
        notice_context = f"--- ARENA NOTICE BOARD ---\n{notice_board}\n\n"

    # Observation
    self_hp = obs.self.get("hp", 150)
    self_max_hp = obs.self.get("max_hp", 150)
    self_cd = int(obs.self.get("action_cooldown", 0) or 0)
    _can_act = obs.self.get("can_act")
    self_locked = (self_cd > 0) if _can_act is None else (not _can_act)
    lock_status = "LOCKED (recovering, action will buffer as standing intent)" if self_locked else "FREE (action executes immediately)"

    # Rivals and bounty status
    rivals = {k: v for k, v in (obs.scoreboard or {}).items() if k != agent_id}
    eliminated = set(getattr(obs, "eliminated_cats", None) or [])
    rival_entries = []
    for cid, sc in sorted(rivals.items()):
        bounty = sc * 2
        status = "[DEAD]" if cid in eliminated else f"[ALIVE - 2x Bounty: +{bounty} pts]"
        rival_entries.append(f"{cid}: {sc} score {status}")
    rival_text = ", ".join(rival_entries) if rival_entries else "none"

    target_legend = format_cardinal_target_legend(obs, agent_id=agent_id)

    observation_context = (
        f"--- CURRENT OBSERVATION (Tick #{obs.tick}) ---\n"
        f"Identity: {agent_id} (Evil Cat | 150 HP Tank)\n"
        f"Health: {self_hp}/{self_max_hp} HP\n"
        f"Position: x={pos.get('x')}, y={pos.get('y')} | Facing: {facing.upper()}\n"
        f"Action State: {lock_status} (cooldown: {self_cd})\n"
        f"Your Current Score: {obs.score}\n"
        f"Rivals & Bounties: {rival_text}\n"
        f"2D Viewport Image: Attached as multimodal image/png (North-Up | YOU=Purple, Strike Cone=Orange, Hit Lock-On=Green, Rival Cat=Red C#, Mouse=Blue M#, Wall=Dark #)\n"
        f"{target_legend}\n"
        f"Visible Entities in Sight (Raw JSON): {json.dumps(entities)}\n"
        f"Legal Actions: {legal}\n"
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


async def query_adk_gemini(
    obs: Observation,
    history: Optional[List[Dict[str, Any]]] = None,
    agent_id: str = "cat-3",
    history_window_size: int = 5,
    capture: Optional[Dict[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
    commander_directive: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[CatTacticalDecision], str, Optional[str]]:
    """Runs one tactical deliberation turn with Google ADK Gemini (multimodal 2D viewport + text)."""
    started = time.monotonic()
    prompt = build_decision_prompt(
        obs=obs,
        history=history,
        agent_id=agent_id,
        history_window_size=history_window_size,
        capture=capture,
        commander_directive=commander_directive,
    )

    if not has_gemini_credentials():
        return None, "heuristic_fallback", "no Gemini credentials configured"

    user_id = f"evil_actor_{agent_id}"
    session_id = f"session_evil_actor_{agent_id}"

    try:
        try:
            await session_service.delete_session(app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id)
        except Exception:
            pass
        await session_service.create_session(app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id)

        viewport_png = render_viewport_png(obs, agent_id=agent_id)
        msg = types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=viewport_png, mime_type="image/png"),
                types.Part.from_text(text=prompt),
            ],
        )
        effective_timeout = timeout_seconds or get_deliberation_timeout()

        async def _run_stream() -> Tuple[Optional[CatTacticalDecision], Optional[int]]:
            final_decision: Optional[CatTacticalDecision] = None
            thoughts_tokens: Optional[int] = None
            agen = adk_runner.run_async(user_id=user_id, session_id=session_id, new_message=msg)
            try:
                while True:
                    ev = await agen.__anext__()
                    if ev.error_code or ev.error_message:
                        logger.warning("Evil Cat ADK Error: %s - %s", ev.error_code, ev.error_message)
                    usage = getattr(ev, "usage_metadata", None)
                    spent = getattr(usage, "thoughts_token_count", None) if usage else None
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

        if capture is not None:
            capture["latency_ms"] = round((time.monotonic() - started) * 1000)
            capture["thoughts_token_count"] = thoughts_tokens

        if decision is not None:
            clear_auth_error()
            return decision, "adk_gemini", None

    except asyncio.TimeoutError:
        reason = f"Evil Cat deliberation timed out after {timeout_seconds or get_deliberation_timeout()}s"
        logger.warning(reason)
        return None, "heuristic_fallback", reason
    except Exception as exc:
        err = str(exc)
        mark_auth_error(err)
        logger.warning("Evil Cat deliberation error: %s", err)
        return None, "heuristic_fallback", err

    return None, "heuristic_fallback", "Gemini returned empty decision"


async def interrogate_adk_gemini(
    user_prompt: str,
    obs: Optional[Observation] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    agent_id: str = "cat-3",
    history_window_size: int = 5,
    active_directive: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, Optional[str]]:
    """Answers a Commander radio interrogation in character as Evil Cat."""
    clean = (user_prompt or "").strip()
    if not clean:
        return ("Commander, I received an empty transmission.", "heuristic_fallback", None)

    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        return (
            f"Commander, Evil Cat ({agent_id}) reporting in offline mode. Hunting high-value rival cats.",
            "heuristic_fallback",
            None,
        )

    if not has_gemini_credentials():
        return (
            f"Commander, Evil Cat ({agent_id}) is on System-1 reflex (no Gemini credentials). Hunting rival cats.",
            "heuristic_fallback",
            None,
        )

    user_id = f"evil_interrogator_{agent_id}"
    session_id = f"session_evil_interrogator_{agent_id}"
    try:
        try:
            await session_service.delete_session(
                app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id
            )
        except Exception:
            pass
        await session_service.create_session(
            app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id
        )

        pos = obs.self.get("position", {}) if obs else {}
        hp = obs.self.get("hp", 150) if obs else 150
        prompt = (
            f"Identity: {agent_id} (Evil Cat | {hp}/150 HP | Pos {pos})\n"
            f"Active Directive: {active_directive or '(None)'}\n"
            f"Commander Question: \"{clean}\"\n"
            "Reply concisely in character."
        )
        msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        chunks: List[str] = []
        async with asyncio.timeout(10.0):
            agen = adk_interrogator_runner.run_async(
                user_id=user_id, session_id=session_id, new_message=msg
            )
            try:
                while True:
                    ev = await agen.__anext__()
                    if ev.content and ev.content.parts:
                        for p in ev.content.parts:
                            if p.text and not getattr(p, "thought", False):
                                chunks.append(p.text)
            except StopAsyncIteration:
                pass
        reply = "".join(chunks).strip()
        if reply:
            return reply, "adk_gemini", None
        return ("Commander, standing by to eliminate rival cats.", "adk_gemini", None)
    except Exception as exc:
        return (
            f"Commander, radio interference ({exc}). Continuing hunt on System-1 reflex.",
            "heuristic_fallback",
            str(exc),
        )

