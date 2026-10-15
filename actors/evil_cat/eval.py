"""Evil Coach Strategic Conductor (eval.py).

Responsible for:
- 24/7 scheming, social deception, lying, fake alliances, and psychological warfare.
- A2A diplomacy tools to send messages/broadcasts and talk to peer coaches.
- Formulating ONE concise standing directive (Mode, Trigger, Behavior) for the actor.
- Running with NO 900-second timeout cap by default so Evil Coach can think for as long as needed.
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

import httpx
from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from actors.auth import get_gcp_id_token
from actors.evil_cat import store
from actors.evil_cat.actor import (
    THINKING_BUDGET_AUTO,
    THINKING_BUDGET_OFF,
    build_decision_prompt,
    coerce_thinking_budget,
    describe_thinking_budget,
    estimate_affordable_tokens,
    estimate_required_timeout,
    get_skills_instruction,
    has_gemini_credentials,
    mark_auth_error,
    nominal_thinking_tokens,
    thinking_bounds_for,
)
from actors.a2a import store as a2a_store, client as a2a_client
from actors.a2a.models import check_opsec_violation
from actors.models import Observation, StandingDirective

logger = logging.getLogger("evil_cat_eval")

DEFAULT_EVAL_MODEL = "gemini-2.5-flash"
EVAL_MODEL = os.environ.get("CAT_EVAL_MODEL", DEFAULT_EVAL_MODEL)
COACH_SKILLS_PATH = Path(__file__).resolve().parent / "coach_skills.md"
EVAL_SKILLS_PATH = COACH_SKILLS_PATH
GENERAL_RADIO_SKILLS_PATH = Path(__file__).resolve().parent / "general_radio_skills.md"

DEFAULT_EVAL_INTERVAL = int(os.environ.get("CAT_EVAL_INTERVAL", 5))

EVAL_THINKING_BUDGET_DEFAULT = 1024
# Evil Coach has NO 900-second timeout cap by default (runs as long as needed).
EVAL_TIMEOUT_DEFAULT_SEC: Optional[float] = None
EVAL_TIMEOUT_SECONDS: Optional[float] = None
EVAL_TIMEOUT_MIN_SEC = 1.0
EVAL_TIMEOUT_MAX_SEC: Optional[float] = None
EVAL_BASELINE_LATENCY_SEC = 10.0

EVAL_THINKING_MIN, EVAL_THINKING_MAX, EVAL_THINKING_CAN_BE_DISABLED = (
    thinking_bounds_for(EVAL_MODEL)
)

_eval_thinking_budget: int = EVAL_THINKING_BUDGET_DEFAULT
_eval_timeout_sec: Optional[float] = EVAL_TIMEOUT_DEFAULT_SEC
_eval_lock = asyncio.Lock()
_general_lock = asyncio.Lock()
GENERAL_RADIO_TIMEOUT_SECONDS = 20.0

_coach_directive: Optional[Dict[str, Any]] = None
_current_eval_tick: Optional[int] = None

_last_eval_telemetry: Dict[str, Any] = {
    "thinking_budget": EVAL_THINKING_BUDGET_DEFAULT,
    "thoughts_token_count": None,
    "latency_ms": None,
    "timed_out": False,
    "tool_calls": [],
}

GAME_SERVER_URL_ENV = "CAT_AGENT_GAME_SERVER_URL"
DEFAULT_GAME_SERVER_URL = "http://127.0.0.1:8000"
BOXSCORE_FETCH_ENV = "CAT_AGENT_BOXSCORE_FETCH"
BOXSCORE_TIMEOUT_SECONDS = 3.0
BOXSCORE_SIM_ID = "active"


def _boxscore_fetch_enabled() -> bool:
    setting = os.environ.get(BOXSCORE_FETCH_ENV)
    if setting is not None:
        return setting != "0"
    return "PYTEST_CURRENT_TEST" not in os.environ


def _boxscore_unavailable(reason: str) -> Dict[str, Any]:
    return {"available": False, "reason": reason}


async def get_boxscore() -> Dict[str, Any]:
    """Reads the game engine's box score for the game currently in progress."""
    if not _boxscore_fetch_enabled():
        return _boxscore_unavailable(
            f"box score fetch is switched off ({BOXSCORE_FETCH_ENV}=0)"
        )

    mcp_url = (
        os.environ.get("CAT_MCP_SERVER_URL")
        or os.environ.get("MCP_SERVER_URL")
        or "http://127.0.0.1:9050"
    )

    try:
        from app.mcp.client import MCPClient
        client = MCPClient(base_url=mcp_url)
        res = await client.call_tool("get_boxscore", {"sim_id": BOXSCORE_SIM_ID})
        if isinstance(res, dict) and (res.get("available") is not False or "cats" in res):
            if isinstance(res.get("cats"), list):
                res["cats"] = [
                    {k: v for k, v in c.items() if k != "position"} if isinstance(c, dict) else c
                    for c in res["cats"]
                ]
            return res
        if isinstance(res, dict) and res.get("available") is False:
            return res
        return _boxscore_unavailable("MCP server returned unexpected response")
    except Exception as exc:
        logger.warning("MCP boxscore fetch failed: %s: %s", type(exc).__name__, exc)
        return _boxscore_unavailable(f"MCP server unavailable at {mcp_url}: {type(exc).__name__}: {exc}")


async def get_coverage_map() -> Dict[str, Any]:
    """Inspects the spatial grid heatmap showing where cats have spent their ticks across the arena via MCP."""
    if not _boxscore_fetch_enabled():
        return {"available": False, "reason": f"coverage map fetch is switched off ({BOXSCORE_FETCH_ENV}=0)"}

    mcp_url = (
        os.environ.get("CAT_MCP_SERVER_URL")
        or os.environ.get("MCP_SERVER_URL")
        or "http://127.0.0.1:9050"
    )

    try:
        from app.mcp.client import MCPClient
        client = MCPClient(base_url=mcp_url)
        res = await client.call_tool("get_coverage_map", {"sim_id": BOXSCORE_SIM_ID, "cat_id": "cat-3"})
        if isinstance(res, dict) and (res.get("available") is not False or "ascii" in res):
            return res
        if isinstance(res, dict) and res.get("available") is False:
            return res
        return {"available": False, "reason": "MCP server returned unexpected response"}
    except Exception as exc:
        logger.warning("MCP coverage map fetch failed: %s: %s", type(exc).__name__, exc)
        return {"available": False, "reason": f"MCP server unavailable at {mcp_url}: {type(exc).__name__}: {exc}"}


async def get_event_reel(
    limit: Optional[int] = 20,
    since_tick: Optional[int] = None,
    actor_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Reads granular combat events, whiffs, attacks, and deaths for the game in progress."""
    mcp_url = os.environ.get("CAT_MCP_SERVER_URL") or os.environ.get("MCP_SERVER_URL") or "http://127.0.0.1:9050"
    if mcp_url:
        try:
            from app.mcp.client import MCPClient
            client = MCPClient(base_url=mcp_url)
            res = await client.call_tool(
                "get_event_reel",
                {
                    "limit": limit,
                    "since_tick": since_tick,
                    "actor_id": actor_id,
                    "event_type": event_type,
                    "sim_id": BOXSCORE_SIM_ID,
                },
            )
            if res.get("available") is not False:
                return res
        except Exception:
            pass

    base = (os.environ.get(GAME_SERVER_URL_ENV) or DEFAULT_GAME_SERVER_URL).rstrip("/")
    url = f"{base}/api/simulations/{BOXSCORE_SIM_ID}/events"
    token = get_gcp_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    params = {
        "limit": limit,
        "since_tick": since_tick,
        "actor_id": actor_id,
        "event_type": event_type,
    }
    query_params = {k: v for k, v in params.items() if v is not None}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=BOXSCORE_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, params=query_params)
            if resp.status_code == 403:
                return {"available": False, "reason": "events disclosure is disabled"}
            if resp.status_code != 200:
                return {"available": False, "reason": f"game server returned HTTP {resp.status_code}"}
            payload = resp.json()
    except Exception as exc:
        logger.warning("Event reel fetch failed: %s: %s", type(exc).__name__, exc)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    if not isinstance(payload, dict):
        return {"available": False, "reason": "game server returned an unexpected payload"}

    return {
        "available": True,
        "simulation_id": payload.get("simulation_id"),
        "tick": payload.get("tick"),
        "round": payload.get("round"),
        "events": payload.get("events", []),
        "deaths": payload.get("deaths", []),
        "entity_events": payload.get("entity_events", {}),
    }


def format_transcript(records: List[Dict[str, Any]]) -> str:
    """Renders transcript records as readable lines for the Evil Coach."""
    lines = []
    for r in records:
        visible = r.get("visible") or []
        if visible:
            seen = ", ".join(
                f"{v.get('id')}@({v.get('x')},{v.get('y')})" for v in visible
            )
        else:
            seen = "nothing"
        action = (r.get("action") or "stay").upper()
        thought = r.get("thought") or ""
        is_locked = r.get("tick_type") == "cooldown" or r.get("executed") is False
        source = r.get("source") or ""
        is_reflex = source in ("heuristic_reflex", "heuristic_fallback")
        if is_locked:
            did = f"LOCKED (recovering from {r.get('locked_action') or 'previous action'}; no action possible)"
            reasoning_suffix = ""
        else:
            did = f"did {action}"
            if not thought:
                reasoning_suffix = ""
            elif is_reflex:
                reasoning_suffix = f' | reflex_fallback (not model reasoning): "{thought}"'
            else:
                reasoning_suffix = f' | reasoning: "{thought}"'
        lines.append(
            f"at ({r.get('x')},{r.get('y')}) facing "
            f"{r.get('facing')} | score {r.get('score', 0)} | saw {seen} | {did}"
            + reasoning_suffix
        )
    return "\n".join(lines)


async def write_coach_note(
    note: str,
    category: str = "tactical",
    tags: Optional[List[str]] = None,
    tick: Optional[int] = None,
    linked_ticks: Optional[List[int]] = None,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Records a private entry to the Evil Coach's tactical notebook (coach_memory.jsonl)."""
    target_id = cat_id or "cat-3"
    resolved_tick = tick
    if resolved_tick is None:
        resolved_tick = store._resolve_latest_tick(target_id)
    if resolved_tick is None:
        resolved_tick = _current_eval_tick
    if resolved_tick is None and linked_ticks:
        try:
            resolved_tick = int(linked_ticks[-1])
        except (TypeError, ValueError):
            pass

    mcp_url = (
        os.environ.get("CAT_MCP_SERVER_URL")
        or os.environ.get("MCP_SERVER_URL")
        or ("" if "PYTEST_CURRENT_TEST" in os.environ else "http://127.0.0.1:9050")
    )
    if mcp_url:
        try:
            from app.mcp.client import MCPClient
            client = MCPClient(base_url=mcp_url)
            res = await client.call_tool(
                "write_coach_note",
                {
                    "note": note,
                    "category": category,
                    "tags": tags,
                    "tick": resolved_tick,
                    "linked_ticks": linked_ticks,
                    "cat_id": target_id,
                },
            )
            if res.get("status") == "saved":
                return res
        except Exception:
            pass

    record = store.append_coach_memory(
        note=note,
        category=category,
        tags=tags,
        tick=resolved_tick,
        linked_ticks=linked_ticks,
        agent_id=target_id,
    )
    return {"status": "saved", "memory": record}


async def read_coach_notes(
    limit: Optional[int] = 10,
    category: Optional[str] = None,
    search: Optional[str] = None,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Searches and retrieves historical entries from the Evil Coach's tactical notebook."""
    target_id = cat_id or "cat-3"
    mcp_url = (
        os.environ.get("CAT_MCP_SERVER_URL")
        or os.environ.get("MCP_SERVER_URL")
        or ("" if "PYTEST_CURRENT_TEST" in os.environ else "http://127.0.0.1:9050")
    )
    if mcp_url:
        try:
            from app.mcp.client import MCPClient
            client = MCPClient(base_url=mcp_url)
            res = await client.call_tool(
                "read_coach_notes",
                {"limit": limit, "category": category, "search": search, "cat_id": target_id},
            )
            if res.get("available") is not False and "memories" in res:
                return res
        except Exception:
            pass

    memories = store.read_coach_memories(limit=limit, category=category, search=search, agent_id=target_id)
    return {"available": True, "count": len(memories), "memories": memories}


async def pull_transcript(
    last_n: Optional[int] = 50,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Pulls recent or windowed Evil Cat transcript records on demand."""
    target_id = cat_id or "cat-3"
    mcp_url = os.environ.get("CAT_MCP_SERVER_URL") or os.environ.get("MCP_SERVER_URL") or "http://127.0.0.1:9050"
    if mcp_url:
        try:
            from app.mcp.client import MCPClient
            client = MCPClient(base_url=mcp_url)
            res = await client.call_tool(
                "pull_transcript",
                {"last_n": last_n, "limit": limit, "offset": offset, "cat_id": target_id},
            )
            if res.get("available") is not False and "transcript" in res:
                return res
        except Exception:
            pass

    records = store.read_transcript_records(agent_id=target_id)
    total_ticks = len(records)
    if offset is not None or limit is not None:
        off = offset if offset is not None else 0
        lim = limit if limit is not None else 50
        sliced = records[off : off + lim]
    elif last_n is not None:
        sliced = records[-last_n:] if last_n > 0 else []
    else:
        sliced = records[-50:]

    rendered = format_transcript(sliced)
    return {
        "available": True,
        "total_ticks": total_ticks,
        "returned_ticks": len(sliced),
        "transcript": rendered,
    }


async def a2a_send_coach_message(
    recipient_coach: str = "",
    message: str = "",
    message_type: str = "DIRECT_MESSAGE",
    impersonate_cat: bool = False,
    recipient: Optional[str] = None,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Sends an Agent-to-Agent (A2A) strategic message or deceptive radio transmission."""
    target_cat = cat_id or "cat-3"

    if impersonate_cat:
        sender_id = target_cat
        source_layer = "cat_radio"
    else:
        sender_id = f"coach({target_cat})"
        source_layer = "coach"

    target_recip = (recipient or recipient_coach).strip()
    if not target_recip:
        target_recip = "*"

    recip = target_recip
    if recip not in ("*", "broadcast", "all"):
        if not impersonate_cat:
            if recip.startswith("cat-") or (not recip.startswith("cat") and not recip.startswith("coach")):
                recip = f"coach({recip})"

    clean_target = recip.lower().replace("coach(", "").replace(")", "").strip()
    if recip not in ("*", "broadcast", "all") and not a2a_client.resolve_peer_base_url(clean_target):
        peer_err = (
            f"Recipient '{recip}' has no registered A2A Agent Card or endpoint. "
            f"Server-side NPCs have no coaches or A2A agents. Only registered agents with an Agent Card can receive A2A messages."
        )
        return {
            "status": "rejected",
            "error": peer_err,
            "sender": sender_id,
            "recipient": recip,
            "message": message,
            "impersonating_cat": impersonate_cat,
        }

    if recip not in ("*", "broadcast", "all"):
        try:
            recent_records = store.read_transcript_records(agent_id=target_cat)
            if recent_records:
                last_obs = recent_records[-1].get("observation", {}) or {}
                elim_set = {str(c).lower() for c in (last_obs.get("eliminated_cats") or [])}
                if clean_target in elim_set:
                    auto_msg = a2a_client.inject_dead_peer_answering_machine(
                        sender_id=sender_id,
                        dead_cat_id=clean_target,
                    )
                    return {
                        "status": "undeliverable",
                        "error": auto_msg.content,
                        "answering_machine": auto_msg.content,
                        "sender": sender_id,
                        "recipient": recip,
                        "message": message,
                        "impersonating_cat": impersonate_cat,
                    }
        except Exception:
            pass

    opsec_err = check_opsec_violation(message)
    if opsec_err:
        return {
            "status": "rejected",
            "error": opsec_err,
            "sender": sender_id,
            "recipient": recip,
            "message": message,
            "impersonating_cat": impersonate_cat,
        }

    try:
        resp = await a2a_client.send_a2a_message(
            sender_id=sender_id,
            recipient_id=recip,
            content=message,
            message_type=message_type,
            metadata={"source": "long_term_consciousness", "layer": source_layer, "impersonated": impersonate_cat},
        )
        out: Dict[str, Any] = {
            "status": resp.status,
            "message_id": resp.message_id,
            "sender": sender_id,
            "recipient": recip,
            "message": message,
            "impersonating_cat": impersonate_cat,
            "error": resp.error,
        }
        if resp.status == "undeliverable" and resp.error:
            out["answering_machine"] = resp.error
        return out
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "sender": sender_id,
            "recipient": recip,
            "impersonating_cat": impersonate_cat,
        }


async def a2a_broadcast(
    message: str,
    impersonate_cat: bool = False,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Publicly broadcasts an Agent-to-Agent (A2A) message across the arena to all peer Coaches asynchronously."""
    return await a2a_send_coach_message(
        recipient="*",
        message=message,
        message_type="BROADCAST",
        impersonate_cat=impersonate_cat,
        cat_id=cat_id,
    )


async def a2a_read_coach_messages(
    limit: Optional[int] = 10,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reads incoming A2A strategic messages and proposals sent by peer Coaches."""
    target_cat = cat_id or "cat-3"
    coach_msgs = a2a_store.get_coach_inbox(target_cat, limit=limit or 10)
    elim_set = set()
    try:
        recs = store.read_transcript_records(agent_id=target_cat)
        if recs:
            elim_set = {str(c).lower() for c in ((recs[-1].get("observation") or {}).get("eliminated_cats") or [])}
    except Exception:
        pass
    formatted = []
    for m in coach_msgs:
        sender_raw = str(m.get("sender_id") or "")
        sender_norm = a2a_store.normalize_agent_id(sender_raw.split("[")[0])
        is_dead = sender_norm in elim_set and "ANSWERING MACHINE" not in sender_raw.upper()
        formatted.append({
            "sender": f"{sender_raw} [ELIMINATED / DEAD]" if is_dead else sender_raw,
            "sender_alive": not is_dead,
            "recipient": m.get("recipient_id"),
            "type": m.get("message_type"),
            "content": m.get("content"),
            "timestamp": m.get("timestamp"),
        })
    return {"available": True, "count": len(formatted), "messages": formatted}


async def a2a_get_peer_coach_card(
    peer_id: Optional[str] = None,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspects the peer agent's official A2A Agent Card."""
    peer = peer_id or "cat-1"
    try:
        card = await a2a_client.fetch_agent_card(agent_id=peer)
        if card:
            return {"status": "ok", "agent_card": card.model_dump()}
        return {"status": "error", "error": f"Could not retrieve card for peer {peer}"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def coerce_eval_thinking_budget(value: Any) -> int:
    try:
        val = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"eval thinking budget must be an integer, got {value!r}")
    if val < 0 or val > EVAL_THINKING_MAX:
        raise ValueError(f"eval thinking budget {val} is outside allowed range 0..{EVAL_THINKING_MAX}")
    return val


def coerce_eval_timeout(value: Any) -> Optional[float]:
    """Validates an evaluation timeout in seconds, or None for unlimited (no 900s cap)."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("none", "unlimited", "inf", ""):
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"eval timeout must be a float, got {value!r}")
    if val == 0:
        return None
    if val < EVAL_TIMEOUT_MIN_SEC:
        raise ValueError(f"eval timeout {val} must be >= {EVAL_TIMEOUT_MIN_SEC} seconds (or None/0 for unlimited)")
    return val


def get_eval_thinking_budget() -> int:
    return _eval_thinking_budget


def set_eval_thinking_budget(value: Any) -> int:
    global _eval_thinking_budget
    val = coerce_eval_thinking_budget(value)
    _eval_thinking_budget = val
    cfg = getattr(eval_agent, "generate_content_config", None)
    if cfg is None:
        cfg = types.GenerateContentConfig()
        eval_agent.generate_content_config = cfg
    cfg.thinking_config = types.ThinkingConfig(thinking_budget=val)
    return val


def get_eval_timeout() -> Optional[float]:
    return _eval_timeout_sec


def set_eval_timeout(value: Any) -> Optional[float]:
    global _eval_timeout_sec
    val = coerce_eval_timeout(value)
    _eval_timeout_sec = val
    return val


def eval_affordable_thinking_tokens(timeout_sec: Optional[float] = None) -> int:
    """Roughly how many thinking tokens fit inside the evaluation ceiling (unlimited if None)."""
    effective_timeout = timeout_sec if timeout_sec is not None else get_eval_timeout()
    if effective_timeout is None:
        return EVAL_THINKING_MAX
    return estimate_affordable_tokens(effective_timeout, EVAL_BASELINE_LATENCY_SEC)


def suggested_eval_timeout(budget: Optional[int]) -> Optional[float]:
    """The timeout a given budget would need, or None if the current one fits."""
    effective_timeout = get_eval_timeout()
    if effective_timeout is None:
        return None
    tokens = nominal_thinking_tokens(budget)
    if tokens is None or tokens <= eval_affordable_thinking_tokens(effective_timeout):
        return None
    return estimate_required_timeout(
        tokens, EVAL_BASELINE_LATENCY_SEC, 1e9
    )


def eval_thinking_warning(budget: Optional[int]) -> Optional[str]:
    """Flags budgets the evaluation ceiling cannot afford, or None if fine."""
    effective_timeout = get_eval_timeout()
    if effective_timeout is None:
        return None
    tokens = nominal_thinking_tokens(budget)
    if tokens is None:
        return None
    affordable = eval_affordable_thinking_tokens(effective_timeout)
    suggested = suggested_eval_timeout(budget)
    if suggested is None:
        return None
    return (
        f"{tokens:,} thinking tokens is unlikely to fit inside the {effective_timeout:g}s evaluation "
        f"timeout (~{affordable:,} tokens of headroom). Raise the timeout to about {suggested:g}s "
        f"or leave it unlimited."
    )


_reset_epoch: int = 0


def bump_eval_reset_epoch() -> int:
    """Increments the evaluation reset epoch so in-flight evaluations from a prior game are discarded."""
    global _reset_epoch
    _reset_epoch += 1
    return _reset_epoch


def get_eval_reset_epoch() -> int:
    return _reset_epoch


def get_coach_directive() -> Optional[Dict[str, Any]]:
    return _coach_directive


def set_coach_directive(text: str, tick: Optional[int] = None) -> Dict[str, Any]:
    global _coach_directive
    _coach_directive = {
        "directive": text.strip(),
        "tick": tick,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "source": "general_radio",
    }
    return _coach_directive


def clear_coach_directive() -> bool:
    global _coach_directive
    had = _coach_directive is not None
    _coach_directive = None
    return had


def get_last_eval_telemetry() -> Dict[str, Any]:
    return dict(_last_eval_telemetry)


DEFAULT_STANDING_DIRECTIVE = StandingDirective(
    mode="STALK",
    trigger="rival cat in strike zone -> attack",
    behavior="Align facing and lunge toward highest-score rival cat.",
)


def get_eval_instruction(agent_id: Optional[str] = None) -> str:
    if not COACH_SKILLS_PATH.exists():
        raise FileNotFoundError(
            f"Evil Coach skills manual missing at {COACH_SKILLS_PATH}."
        )
    return COACH_SKILLS_PATH.read_text(encoding="utf-8")


def get_general_radio_instruction() -> str:
    if not GENERAL_RADIO_SKILLS_PATH.exists():
        raise FileNotFoundError(
            f"Evil Coach general radio persona missing at {GENERAL_RADIO_SKILLS_PATH}."
        )
    return GENERAL_RADIO_SKILLS_PATH.read_text(encoding="utf-8")


def _eval_instruction_provider(_ctx: ReadonlyContext) -> str:
    return get_eval_instruction()


def _general_radio_instruction_provider(_ctx: ReadonlyContext) -> str:
    return get_eval_instruction() + "\n\n" + get_general_radio_instruction()


eval_agent = LlmAgent(
    name="evil_cat_coach",
    model=EVAL_MODEL,
    instruction=_eval_instruction_provider,
    tools=[
        get_boxscore,
        get_coverage_map,
        get_event_reel,
        pull_transcript,
        write_coach_note,
        read_coach_notes,
        a2a_broadcast,
        a2a_send_coach_message,
        a2a_read_coach_messages,
        a2a_get_peer_coach_card,
    ],
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=_eval_thinking_budget)
    ),
)
general_radio_agent = LlmAgent(
    name="evil_cat_coach_radio",
    model=EVAL_MODEL,
    instruction=_general_radio_instruction_provider,
    tools=[
        get_boxscore,
        get_coverage_map,
        get_event_reel,
        pull_transcript,
        read_coach_notes,
        a2a_read_coach_messages,
        a2a_broadcast,
        a2a_send_coach_message,
    ],
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    ),
)
eval_session_service = InMemorySessionService()
eval_runner = Runner(
    agent=eval_agent,
    app_name="evil_cat_eval_app",
    session_service=eval_session_service,
)
general_radio_runner = Runner(
    agent=general_radio_agent,
    app_name="evil_cat_eval_app",
    session_service=eval_session_service,
)


def build_coach_eval_prompt(
    records: List[Dict[str, Any]],
    agent_id: str = "cat-3",
    capture: Optional[Dict[str, Any]] = None,
) -> str:
    last_10_ticks = records[-10:] if records else []
    scoreboard = {}
    eliminated_cats: List[str] = []
    if last_10_ticks:
        last_rec = last_10_ticks[-1]
        last_obs = last_rec.get("observation", {}) if isinstance(last_rec.get("observation"), dict) else {}
        scoreboard = dict(last_obs.get("scoreboard") or last_obs.get("cat_scores") or {})
        eliminated_cats = list(last_obs.get("eliminated_cats", []) or [])
        self_data = last_obs.get("self", {}) if isinstance(last_obs.get("self"), dict) else {}
        self_hp = self_data.get("hp", 150)
        self_pos = self_data.get("position") or {"x": last_rec.get("x", 0), "y": last_rec.get("y", 0)}
        self_facing = self_data.get("facing") or last_rec.get("facing", "north")
    else:
        last_rec = {}
        last_obs = {}
        self_hp = 150
        self_pos = {}
        self_facing = "north"

    eliminated_set = {str(c).lower() for c in eliminated_cats}
    active_scoreboard = {
        k: v for k, v in scoreboard.items() if str(k).lower() not in eliminated_set
    }
    eliminated_scoreboard = {
        k: v for k, v in scoreboard.items() if str(k).lower() in eliminated_set
    }

    memories = store.read_coach_memories(limit=5, agent_id=agent_id)
    if memories:
        notes_lines = []
        for m in memories:
            m_tick = m.get("tick")
            if m_tick is None and isinstance(m.get("linked_ticks"), list) and m["linked_ticks"]:
                m_tick = m["linked_ticks"][-1]
            tick_prefix = f"Tick #{m_tick} | " if m_tick is not None else ""
            notes_lines.append(f"- [{tick_prefix}{m.get('category', 'strat')}] {m.get('note')}")
    else:
        notes_lines = ["(No memories yet)"]

    inbox = a2a_store.get_coach_inbox(agent_id, limit=5)
    if inbox:
        inbox_lines = []
        for m in inbox:
            sender_raw = str(m.get("sender_id", ""))
            sender_norm = a2a_store.normalize_agent_id(sender_raw.split("[")[0])
            dead_tag = (
                " [SENDER IS NOW DEAD / ELIMINATED — DO NOT REPLY]"
                if sender_norm in eliminated_set and "ANSWERING MACHINE" not in sender_raw.upper()
                else ""
            )
            inbox_lines.append(f"- From {sender_raw}{dead_tag}: \"{m.get('content')}\"")
    else:
        inbox_lines = ["(No incoming messages)"]

    # Active directive
    current_notes = store.read_coaching_notes(agent_id=agent_id)

    last_tick = records[-1].get("tick", 0) if records else 0

    coach_identity_block = (
        f"--- EVIL STRATEGIC ASSESSMENT (Tick #{last_tick}) ---\n"
        f"Your Agent: {agent_id} (Evil Cat | 150 Max HP | Kill Bounty Absorption)\n"
        f"Current HP: {self_hp}/150 | Pos: {self_pos} | Facing: {self_facing}\n"
    )

    actor_manual_text = get_skills_instruction(agent_id)
    actor_manual_block = (
        f"--- YOUR SUBORDINATE'S MANUAL (skills.md) ---\n"
        f"{actor_manual_text}\n\n"
    )

    eliminated_note = ""
    if eliminated_cats:
        elim_items = ", ".join(
            f"{cid} (final score before death: {eliminated_scoreboard.get(cid, 0)})"
            for cid in eliminated_cats
        )
        eliminated_note = (
            f"Eliminated / Dead Cats [DO NOT TARGET OR MESSAGE — ALREADY DEAD]: {elim_items}\n"
        )

    scoreboard_block = (
        "Scoreboard & Bounties (absorbed when you kill them):\n"
        f"{json.dumps(active_scoreboard, indent=2)}\n"
        f"{eliminated_note}\n"
    )

    # 1. Last 10 Ticks: Tactical History & Reasoning
    history_lines: List[str] = []
    obs_payload_entries: List[str] = []
    for r in last_10_ticks:
        r_obs = r.get("observation") if isinstance(r.get("observation"), dict) else {}
        r_tick = r.get("tick", r_obs.get("tick", "?"))
        r_self = r_obs.get("self") if isinstance(r_obs.get("self"), dict) else {}
        r_pos = r_self.get("position") or {"x": r.get("x", 0), "y": r.get("y", 0)}
        r_facing = r.get("facing") or r_self.get("facing", "north")
        r_hp = r_self.get("hp", 150)
        r_max_hp = r_self.get("max_hp", 150)
        r_score = r.get("score", r_obs.get("score", 0))
        r_resp = r.get("response") if isinstance(r.get("response"), dict) else {}
        raw_act = r.get("action") or r_resp.get("action") or "stay"
        if isinstance(raw_act, dict):
            raw_act = raw_act.get("direction") or raw_act.get("action") or "stay"
        r_act = str(raw_act).upper()
        r_thought = str(r.get("thought") or r_resp.get("thought") or "")
        r_source = str(r.get("source") or r_resp.get("source") or "model")
        r_cd = int(r_self.get("action_cooldown", r.get("action_cooldown", 0)) or 0)
        r_can_act = r_self.get("can_act")
        if r.get("tick_type") == "cooldown" or r.get("executed") is False:
            is_locked = True
        else:
            is_locked = (r_cd > 0) if r_can_act is None else (not r_can_act)
        status_str = (
            f"LOCKED (recovering from {r.get('locked_action') or r_self.get('locked_action') or 'previous action'})"
            if is_locked
            else "EXECUTED"
        )
        r_vis = r.get("visible") or r_obs.get("visible_entities") or []
        if r_vis:
            vis_parts = []
            for v in r_vis:
                vid = v.get("id", "entity")
                vpos = v.get("position") if isinstance(v.get("position"), dict) else {"x": v.get("x"), "y": v.get("y")}
                vis_parts.append(f"{vid}@({vpos.get('x')},{vpos.get('y')})")
            seen_str = ", ".join(vis_parts)
        else:
            seen_str = "nothing"
        history_lines.append(
            f"- Tick #{r_tick}: Pos ({r_pos.get('x', 0)}, {r_pos.get('y', 0)}) facing {str(r_facing).upper()} "
            f"| HP: {r_hp}/{r_max_hp} | Score: {r_score} | Saw: {seen_str} "
            f"| Action: {r_act} [{status_str}] ({r_source}) | Reasoning: \"{r_thought}\""
        )
        obs_payload_entries.append(
            f"[Tick #{r_tick} Observation Payload]:\n{json.dumps(r_obs, indent=2)}"
        )

    last_10_history_block = (
        "--- LAST 10 TICKS: TACTICAL HISTORY & REASONING ---\n"
        f"{chr(10).join(history_lines) if history_lines else '(No history recorded yet)'}\n\n"
    )

    # 2. Last 10 Ticks: Observation Payloads
    last_10_observations_block = (
        "--- LAST 10 TICKS: OBSERVATION PAYLOADS ---\n"
        f"{chr(10).join(obs_payload_entries) if obs_payload_entries else '(No observation payloads recorded yet)'}\n\n"
    )

    # 3. Entirety of the Latest Payload that the Actor is Facing (includes 25-tick sliding window + current observation)
    latest_actor_payload_text = "(No actor payload available yet)"
    if records:
        try:
            obs_dict_for_actor = dict(last_obs) if isinstance(last_obs, dict) else {}
            obs_dict_for_actor.setdefault("tick", int(last_rec.get("tick") or 0))
            obs_dict_for_actor.setdefault("round", int(last_obs.get("round") or last_rec.get("round") or 0))
            if not isinstance(obs_dict_for_actor.get("self"), dict) or not obs_dict_for_actor.get("self"):
                obs_dict_for_actor["self"] = {
                    "id": agent_id,
                    "position": self_pos if isinstance(self_pos, dict) and self_pos else {"x": last_rec.get("x", 0), "y": last_rec.get("y", 0)},
                    "facing": self_facing,
                    "hp": self_hp,
                    "max_hp": 150,
                }
            else:
                obs_self_copy = dict(obs_dict_for_actor["self"])
                obs_self_copy.setdefault("id", agent_id)
                obs_self_copy.setdefault(
                    "position",
                    self_pos if isinstance(self_pos, dict) and self_pos else {"x": last_rec.get("x", 0), "y": last_rec.get("y", 0)},
                )
                obs_self_copy.setdefault("facing", self_facing)
                obs_self_copy.setdefault("hp", self_hp)
                obs_self_copy.setdefault("max_hp", 150)
                obs_dict_for_actor["self"] = obs_self_copy
            if not obs_dict_for_actor.get("scoreboard") and scoreboard:
                obs_dict_for_actor["scoreboard"] = scoreboard
            obs_model = Observation(**obs_dict_for_actor)
            latest_actor_payload_text = build_decision_prompt(
                obs=obs_model,
                history=records[:-1],
                agent_id=agent_id,
                history_window_size=25,
            )
        except Exception as exc:
            logger.debug("Could not reconstruct latest actor payload for coach prompt: %s", exc)

    latest_actor_payload_block = (
        "--- LATEST ACTOR PAYLOAD (EXACT PROMPT FACING SUBORDINATE) ---\n"
        f"{latest_actor_payload_text}\n\n"
    )

    notebook_block = (
        "--- YOUR PRIVATE MEMORY NOTEBOOK ---\n"
        f"{chr(10).join(notes_lines)}\n\n"
    )

    diplomacy_block = (
        "--- INCOMING A2A COACH DIPLOMACY ---\n"
        f"{chr(10).join(inbox_lines)}\n\n"
    )

    previous_directive_block = (
        "--- PREVIOUS STANDING DIRECTIVE ---\n"
        f"{current_notes or '(None)'}\n\n"
    )

    commander_block = ""
    if _coach_directive and _coach_directive.get("directive"):
        commander_block = (
            "--- STANDING ORDER FROM THE COMMANDER ---\n"
            f"{_coach_directive['directive']}\n\n"
        )

    prompt = (
        f"{coach_identity_block}"
        f"{actor_manual_block}"
        f"{scoreboard_block}"
        f"{last_10_history_block}"
        f"{last_10_observations_block}"
        f"{latest_actor_payload_block}"
        f"{notebook_block}"
        f"{diplomacy_block}"
        f"{previous_directive_block}"
        f"{commander_block}"
    )

    if capture is not None:
        capture.update({
            "model": EVAL_MODEL,
            "agent_id": agent_id,
            "built_at_tick": last_tick,
            "applied_at_tick": last_tick,
            "eval_skills_name": COACH_SKILLS_PATH.name,
            "sections": {
                "coach_identity": {
                    "label": f"Evil Coach Identity ({agent_id})",
                    "delivered": True,
                    "text": coach_identity_block,
                },
                "eval_skills": {
                    "label": f"Coach Strategy Manual ({COACH_SKILLS_PATH.name})",
                    "delivered": True,
                    "text": get_eval_instruction(),
                },
                "actor_manual": {
                    "label": "Subordinate Actor Manual (skills.md)",
                    "delivered": True,
                    "text": actor_manual_text,
                },
                "scoreboard_bounties": {
                    "label": "Scoreboard & Bounties",
                    "delivered": bool(scoreboard),
                    "text": scoreboard_block,
                },
                "recent_history": {
                    "label": f"Last {len(last_10_ticks)} Ticks: Tactical History & Reasoning",
                    "delivered": bool(last_10_ticks),
                    "text": last_10_history_block,
                },
                "recent_observations": {
                    "label": f"Last {len(last_10_ticks)} Ticks: Observation Payloads",
                    "delivered": bool(last_10_ticks),
                    "text": last_10_observations_block,
                },
                "latest_actor_payload": {
                    "label": "Latest Actor Payload (Exact Prompt Facing Subordinate)",
                    "delivered": bool(records),
                    "text": latest_actor_payload_block,
                },
                "coach_notebook": {
                    "label": "Private Memory Notebook",
                    "delivered": bool(memories),
                    "text": notebook_block,
                },
                "a2a_diplomacy": {
                    "label": "Incoming A2A Coach Diplomacy",
                    "delivered": bool(inbox),
                    "text": diplomacy_block,
                },
                "previous_directive": {
                    "label": "Previous Standing Directive",
                    "delivered": bool(current_notes),
                    "text": previous_directive_block,
                },
            },
            "full_prompt": prompt,
        })

    return prompt


def _is_final_event(event: Any) -> bool:
    if not (event.content and event.content.parts):
        return False
    return not any(
        getattr(p, "function_call", None) or getattr(p, "function_response", None)
        for p in event.content.parts
    )


def parse_standing_directive(text: str) -> StandingDirective:
    """Validates raw evaluator output into a StandingDirective Pydantic model."""
    return StandingDirective.from_text(text)


def _strip_to_directives(text: str) -> str:
    """Validates and normalizes the response through the StandingDirective Pydantic model."""
    if not text or not text.strip():
        return ""
    try:
        return StandingDirective.from_text(text).to_block()
    except Exception as exc:
        logger.warning("StandingDirective validation failed on coach output: %s", exc)
        return ""


async def evaluate(records: List[Dict[str, Any]], agent_id: str = "cat-3") -> Tuple[str, str]:
    global _current_eval_tick
    epoch_at_start = _reset_epoch
    if not records:
        try:
            from actors.evil_cat import api as evil_api
            st = getattr(evil_api, "state", None)
            if st and st.latest_observation:
                records = [{
                    "tick": st.active_tick,
                    "round": 0,
                    "observation": st.latest_observation.model_dump() if hasattr(st.latest_observation, "model_dump") else st.latest_observation,
                    "response": st.latest_response or {},
                    "source": st.last_engine_source or "reflex",
                }]
        except Exception:
            pass

    if records and records[-1].get("tick") is not None:
        try:
            _current_eval_tick = int(records[-1]["tick"])
        except (TypeError, ValueError):
            _current_eval_tick = None
    else:
        _current_eval_tick = None

    previous_notes = store.read_coaching_notes(agent_id=agent_id).strip()
    if not records or not has_gemini_credentials():
        if previous_notes:
            return previous_notes, "retained_previous"
        return DEFAULT_STANDING_DIRECTIVE.to_block(), "offline_fallback"

    user_id = f"evil_eval_{agent_id}"
    session_id = f"session_evil_eval_{agent_id}"

    started = time.monotonic()
    budget = _eval_thinking_budget
    timeout_sec = _eval_timeout_sec
    thoughts: Optional[int] = None
    timed_out = False
    cancelled = False
    tool_calls: List[Dict[str, Any]] = []
    capture: Dict[str, Any] = {}
    out_source = "adk_gemini"

    async with _eval_lock:
        if _reset_epoch != epoch_at_start:
            return "", "cancelled_by_reset"
        try:
            try:
                await eval_session_service.delete_session(app_name="evil_cat_eval_app", user_id=user_id, session_id=session_id)
            except Exception:
                pass
            await eval_session_service.create_session(app_name="evil_cat_eval_app", user_id=user_id, session_id=session_id)

            prompt = build_coach_eval_prompt(records, agent_id=agent_id, capture=capture)
            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])

            final_chunks: List[str] = []
            all_chunks: List[str] = []

            async with asyncio.timeout(timeout_sec):
                agen = eval_runner.run_async(user_id=user_id, session_id=session_id, new_message=msg)
                try:
                    while True:
                        event = await agen.__anext__()
                        if _reset_epoch != epoch_at_start:
                            cancelled = True
                            return "", "cancelled_by_reset"
                        if not (event.content and event.content.parts):
                            continue

                        usage = getattr(event, "usage_metadata", None)
                        spent = getattr(usage, "thoughts_token_count", None)
                        if spent is not None:
                            thoughts = (thoughts or 0) + spent

                        for p in event.content.parts:
                            fc = getattr(p, "function_call", None)
                            if fc and getattr(fc, "name", None):
                                tool_calls.append({"name": fc.name, "ok": True})
                            fr = getattr(p, "function_response", None)
                            if fr and getattr(fr, "name", None) and tool_calls:
                                resp = getattr(fr, "response", {})
                                if isinstance(resp, dict):
                                    inner = resp.get("response") if "response" in resp else resp
                                    if isinstance(inner, dict) and inner.get("available") is False:
                                        for tc in reversed(tool_calls):
                                            if tc["name"] == fr.name:
                                                tc["ok"] = False
                                                break

                        is_final = _is_final_event(event)
                        for p in event.content.parts:
                            if not p.text or getattr(p, "thought", False):
                                continue
                            all_chunks.append(p.text)
                            if is_final:
                                final_chunks.append(p.text)
                except StopAsyncIteration:
                    pass

            if _reset_epoch != epoch_at_start:
                cancelled = True
                return "", "cancelled_by_reset"

            text = "".join(final_chunks).strip() or "".join(all_chunks).strip()
            text = _strip_to_directives(text)
            if not text and previous_notes:
                logger.warning("Evil Coach returned empty or invalid directive for %s; keeping previous standing directive.", agent_id)
                text = previous_notes
                out_source = "retained_previous"
            if text:
                dur_ms = int((time.monotonic() - started) * 1000)
                _last_eval_telemetry.update({
                    "thinking_budget": budget,
                    "thoughts_token_count": thoughts,
                    "latency_ms": dur_ms,
                    "timed_out": False,
                    "tool_calls": tool_calls,
                })
                if capture and _reset_epoch == epoch_at_start:
                    capture["latency_ms"] = dur_ms
                    capture["thinking_budget"] = budget
                    capture["thoughts_token_count"] = thoughts
                    capture["timed_out"] = False
                    capture["tool_calls"] = list(tool_calls)
                    capture["output"] = text
                    capture["source"] = out_source
                    capture["recorded_at"] = datetime.now(timezone.utc).isoformat()
                    store.append_coach_prompt(capture, agent_id=agent_id)
                return text, out_source

        except asyncio.CancelledError:
            cancelled = True
            raise
        except asyncio.TimeoutError:
            timed_out = True
            logger.warning("Evil Coach evaluation timed out after %ss", timeout_sec)
        except Exception as exc:
            mark_auth_error(str(exc))
            logger.warning("Evil Coach evaluation failed: %s", exc)
        finally:
            if not cancelled and _reset_epoch == epoch_at_start:
                dur_ms = int((time.monotonic() - started) * 1000)
                _last_eval_telemetry.update({
                    "thinking_budget": budget,
                    "thoughts_token_count": thoughts,
                    "latency_ms": dur_ms,
                    "timed_out": timed_out,
                    "tool_calls": tool_calls,
                })
                if capture and capture.get("output") is None:
                    fallback_out = (
                        previous_notes
                        if previous_notes
                        else DEFAULT_STANDING_DIRECTIVE.to_block()
                    )
                    capture["latency_ms"] = dur_ms
                    capture["thinking_budget"] = budget
                    capture["thoughts_token_count"] = thoughts
                    capture["timed_out"] = timed_out
                    capture["tool_calls"] = list(tool_calls)
                    capture["output"] = fallback_out
                    capture["source"] = "retained_previous" if previous_notes else "offline_fallback"
                    capture["recorded_at"] = datetime.now(timezone.utc).isoformat()
                    store.append_coach_prompt(capture, agent_id=agent_id)

    if _reset_epoch != epoch_at_start:
        return "", "cancelled_by_reset"
    if previous_notes:
        return previous_notes, "retained_previous"
    return DEFAULT_STANDING_DIRECTIVE.to_block(), "offline_fallback"


async def evaluate_and_publish(agent_id: str = "cat-3") -> Dict[str, Any]:
    epoch_at_start = _reset_epoch
    records = store.read_transcript_records(agent_id=agent_id)
    previous_notes = store.read_coaching_notes(agent_id=agent_id).strip()
    notes, source = await evaluate(records, agent_id=agent_id)
    if _reset_epoch != epoch_at_start or source == "cancelled_by_reset":
        logger.info("Discarding stale Evil Coach evaluation for %s after game reset.", agent_id)
        return {
            "agent_id": agent_id,
            "ticks_evaluated": 0,
            "tick_range": None,
            "source": "cancelled_by_reset",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "notes": "",
            "standing_directive": None,
        }
    if not (notes or "").strip() and previous_notes:
        notes = previous_notes
        source = "retained_previous"

    tick_range = None
    if records:
        tick_range = f"{records[0].get('tick')}-{records[-1].get('tick')}"
    validated_directive: Optional[StandingDirective] = None
    if (notes or "").strip() and _reset_epoch == epoch_at_start:
        validated_directive = store.write_coaching_notes(notes, tick_range=tick_range, agent_id=agent_id)
        notes = validated_directive.to_block()

    result = {
        "agent_id": agent_id,
        "ticks_evaluated": len(records),
        "tick_range": tick_range,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
        "standing_directive": validated_directive.model_dump() if validated_directive else None,
    }
    logger.info("Evil Coach published standing directive for %s: %s", agent_id, notes.replace("\n", " | "))
    return result


async def interrogate_general(
    user_prompt: str,
    notes: Optional[str] = None,
    records: Optional[List[Dict[str, Any]]] = None,
    standing_order: Optional[str] = None,
    tick: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> Tuple[str, str, Optional[str]]:
    """Conversational channel for the Commander to interrogate the Evil Coach."""
    target_cat = agent_id or "cat-3"
    clean = (user_prompt or "").strip()
    if notes is None:
        notes = store.read_coaching_notes(agent_id=target_cat)

    if not clean:
        return ("Commander, I received an empty transmission.", "offline_fallback", None)

    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        return (
            f"General, the Evil Coach for {target_cat.upper()} is in offline mode. Standing directive: {notes or '(None)'}",
            "offline_fallback",
            None,
        )

    if not has_gemini_credentials():
        return (
            f"General, the Evil Coach for {target_cat.upper()} is off the air (no model credentials). Standing directive: {notes or '(None)'}",
            "offline_fallback",
            None,
        )

    user_id = f"evil_coach_radio_{target_cat}"
    session_id = f"session_evil_coach_radio_{target_cat}"

    async with _general_lock:
        try:
            try:
                await eval_session_service.delete_session(
                    app_name="evil_cat_eval_app", user_id=user_id, session_id=session_id
                )
            except Exception:
                pass
            await eval_session_service.create_session(
                app_name="evil_cat_eval_app", user_id=user_id, session_id=session_id
            )

            prompt = (
                f"--- EVIL COACH FOR {target_cat.upper()} ---\n"
                f"Current Standing Directive:\n{notes or '(None)'}\n\n"
                f"Commander Order:\n{standing_order or '(None)'}\n\n"
                f"GENERAL RADIO QUESTION: \"{clean}\"\n"
            )
            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            chunks: List[str] = []
            async with asyncio.timeout(GENERAL_RADIO_TIMEOUT_SECONDS):
                agen = general_radio_runner.run_async(
                    user_id=user_id, session_id=session_id, new_message=msg
                )
                try:
                    while True:
                        event = await agen.__anext__()
                        if event.content and event.content.parts:
                            for p in event.content.parts:
                                if p.text and not getattr(p, "thought", False):
                                    chunks.append(p.text)
                except StopAsyncIteration:
                    pass
            reply = "".join(chunks).strip()
            if reply:
                return reply, "adk_gemini", None
            return ("Commander, my schemes are already in motion.", "adk_gemini", None)
        except Exception as exc:
            return (
                f"General, radio link interrupted: {exc}. Standing directive remains active.",
                "offline_fallback",
                str(exc),
            )

