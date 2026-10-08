"""Evil Cat Tactical Fast Brain (actor.py).

Responsible purely for tick-by-tick tactical execution on the grid:
- No chatting or broadcasting tools (zero network tool latency overhead).
- Lean prompt focusing on immediate perception and the Coach's standing directive.
- Executes the Coach's active Mode, Trigger, and Behavior.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from actors.auth import get_gcp_id_token
from actors.models import CatTacticalDecision, Observation
from actors.cat import store

logger = logging.getLogger("evil_cat_actor")

# Model configuration
DEFAULT_ACTOR_MODEL = "gemini-2.5-flash"
ACTOR_MODEL = os.environ.get("CAT_MODEL", DEFAULT_ACTOR_MODEL)

SKILLS_PATH = Path(__file__).resolve().parent / "skills.md"

THINKING_BUDGET_OFF = 0
THINKING_BUDGET_MIN = 0
THINKING_BUDGET_MAX = 24576
THINKING_BUDGET_DEFAULT = 512
DEFAULT_THINKING_BUDGET_ENV = "CAT_THINKING_BUDGET"

DELIBERATION_TIMEOUT_DEFAULT_SEC = 8.0
DELIBERATION_TIMEOUT_MIN_SEC = 1.0
DELIBERATION_TIMEOUT_MAX_SEC = 120.0


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
    if val < 0 or val > THINKING_BUDGET_MAX:
        raise ValueError(f"thinking budget {val} is outside allowed range 0..{THINKING_BUDGET_MAX}")
    return val


def get_skills_instruction(agent_id: Optional[str] = None) -> str:
    path = SKILLS_PATH
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
            return int(cfg.thinking_config.thinking_budget or 0)
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


def get_deliberation_timeout() -> float:
    return DELIBERATION_TIMEOUT_DEFAULT_SEC


def set_deliberation_timeout(value: Any) -> float:
    return float(value)


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


def has_gemini_credentials() -> bool:
    if os.environ.get("GEMINI_API_KEY"):
        return True
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "True" and os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return True
    return bool(get_gcp_id_token())


def check_gemini_auth_health() -> Dict[str, Any]:
    return {"authenticated": has_gemini_credentials(), "mode": "vertex" if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "True" else "api_key"}


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

    observation_context = (
        f"--- CURRENT OBSERVATION (Tick #{obs.tick}) ---\n"
        f"Identity: {agent_id} (Evil Cat | 150 HP Tank)\n"
        f"Health: {self_hp}/{self_max_hp} HP\n"
        f"Position: x={pos.get('x')}, y={pos.get('y')} | Facing: {facing.upper()}\n"
        f"Action State: {lock_status} (cooldown: {self_cd})\n"
        f"Your Current Score: {obs.score}\n"
        f"Rivals & Bounties: {rival_text}\n"
        f"Visible Entities in Sight: {json.dumps(entities)}\n"
        f"Legal Actions: {legal}\n"
    )

    prompt = f"{history_context}{notice_context}{coaching_context}{observation_context}"

    if capture is not None:
        capture.update({
            "built_at_tick": obs.tick,
            "model": ACTOR_MODEL,
            "thinking_budget": get_thinking_budget(),
            "sections": {
                "skills": {"label": "Evil Cat Skills", "delivered": True, "text": get_skills_instruction(agent_id)},
                "history": {"label": "Recent History", "delivered": bool(history_context), "text": history_context},
                "standing_order": {"label": "Coach Standing Directive (PRIMARY OBJECTIVE)", "delivered": True, "text": coaching_context},
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
) -> Tuple[CatTacticalDecision, str, Optional[Dict[str, Any]]]:
    """Runs one tactical deliberation turn with Google ADK Gemini."""
    started = time.monotonic()
    prompt = build_decision_prompt(
        obs=obs,
        history=history,
        agent_id=agent_id,
        history_window_size=history_window_size,
        capture=capture,
        commander_directive=None,
    )

    if not has_gemini_credentials():
        return CatTacticalDecision(thought="Fallback: No Gemini credentials", action="stay", target_id="", strategy="ROAMING"), "heuristic_fallback", None

    user_id = f"evil_actor_{agent_id}"
    session_id = f"session_evil_actor_{agent_id}"

    try:
        try:
            await session_service.delete_session(app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id)
        except Exception:
            pass
        await session_service.create_session(app_name="evil_cat_agent_app", user_id=user_id, session_id=session_id)

        msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        effective_timeout = timeout_seconds or DELIBERATION_TIMEOUT_DEFAULT_SEC

        async def _run_stream():
            final_decision = None
            async for ev in adk_runner.run_async(user_id=user_id, session_id=session_id, message=msg):
                if ev.is_message():
                    content = ev.get_message()
                    for p in getattr(content, "parts", []):
                        fn_resp = getattr(p, "function_response", None)
                        if fn_resp and getattr(fn_resp, "response", None):
                            resp_data = fn_resp.response
                            if isinstance(resp_data, dict) and "output" in resp_data:
                                try:
                                    final_decision = CatTacticalDecision.model_validate(resp_data["output"])
                                except Exception:
                                    pass
            return final_decision

        decision = await asyncio.wait_for(_run_stream(), timeout=effective_timeout)
        duration_ms = round((time.monotonic() - started) * 1000, 1)

        if decision is not None:
            telemetry = {
                "agent_id": agent_id,
                "tick": obs.tick,
                "duration_ms": duration_ms,
                "thinking_budget": get_thinking_budget(),
                "action": decision.action,
                "strategy": decision.strategy,
                "target_id": decision.target_id,
            }
            return decision, "adk_gemini", telemetry

    except asyncio.TimeoutError:
        logger.warning("Evil Cat deliberation timed out after %ss", timeout_seconds or DELIBERATION_TIMEOUT_DEFAULT_SEC)
    except Exception as exc:
        logger.warning("Evil Cat deliberation error: %s", exc)

    return CatTacticalDecision(thought="Deliberation fallback", action="stay", target_id="", strategy="ROAMING"), "heuristic_fallback", None
