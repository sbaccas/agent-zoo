"""Evil Coach Strategic Conductor (eval.py).

Responsible for:
- 24/7 scheming, social deception, lying, fake alliances, and psychological warfare.
- A2A diplomacy tools to send messages/broadcasts and talk to peer coaches.
- Formulating ONE concise standing directive (Mode, Trigger, Behavior) for the actor.
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
from actors.cat import store
from actors.a2a import store as a2a_store, client as a2a_client
from actors.a2a.tools import a2a_broadcast
from actors.cat.subagents.eval import (
    a2a_get_peer_coach_card,
    a2a_read_coach_messages,
    a2a_send_coach_message,
    get_boxscore,
    get_coverage_map,
    get_event_reel,
    pull_transcript,
    read_coach_notes,
    write_coach_note,
    has_gemini_credentials,
)

logger = logging.getLogger("evil_cat_eval")

DEFAULT_EVAL_MODEL = "gemini-2.5-flash"
EVAL_MODEL = os.environ.get("CAT_EVAL_MODEL", DEFAULT_EVAL_MODEL)
COACH_SKILLS_PATH = Path(__file__).resolve().parent / "coach_skills.md"

EVAL_THINKING_BUDGET_DEFAULT = 1024
EVAL_TIMEOUT_DEFAULT_SEC = 20.0
EVAL_THINKING_MIN = 0
EVAL_THINKING_MAX = 24576
EVAL_THINKING_CAN_BE_DISABLED = True

_eval_thinking_budget: int = EVAL_THINKING_BUDGET_DEFAULT
_eval_timeout_sec: float = EVAL_TIMEOUT_DEFAULT_SEC
_eval_lock = asyncio.Lock()

_last_eval_telemetry: Dict[str, Any] = {
    "thinking_budget": EVAL_THINKING_BUDGET_DEFAULT,
    "thoughts_token_count": None,
    "latency_ms": None,
    "timed_out": False,
    "tool_calls": [],
}


def coerce_eval_thinking_budget(value: Any) -> int:
    try:
        val = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"eval thinking budget must be an integer, got {value!r}")
    if val < 0 or val > EVAL_THINKING_MAX:
        raise ValueError(f"eval thinking budget {val} is outside allowed range 0..{EVAL_THINKING_MAX}")
    return val


def coerce_eval_timeout(value: Any) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"eval timeout must be a float, got {value!r}")
    if val < 1.0 or val > 120.0:
        raise ValueError(f"eval timeout {val} is outside allowed range 1.0..120.0")
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


def get_eval_timeout() -> float:
    return _eval_timeout_sec


def set_eval_timeout(value: Any) -> float:
    global _eval_timeout_sec
    val = coerce_eval_timeout(value)
    _eval_timeout_sec = val
    return val


def get_last_eval_telemetry() -> Dict[str, Any]:
    return dict(_last_eval_telemetry)


def get_eval_instruction() -> str:
    if COACH_SKILLS_PATH.exists():
        return COACH_SKILLS_PATH.read_text(encoding="utf-8")
    return "You are the Evil Coach (coach(cat-3)). Scheme, deceive, and issue ONE standing directive in MODE / TRIGGER / BEHAVIOR format."


def _eval_instruction_provider(_ctx: ReadonlyContext) -> str:
    return get_eval_instruction()


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
eval_session_service = InMemorySessionService()
eval_runner = Runner(
    agent=eval_agent,
    app_name="evil_cat_eval_app",
    session_service=eval_session_service,
)


def build_coach_eval_prompt(
    records: List[Dict[str, Any]],
    agent_id: str = "cat-3",
    capture: Optional[Dict[str, Any]] = None,
) -> str:
    recent_ticks = records[-15:] if records else []
    scoreboard = {}
    if recent_ticks:
        last_obs = recent_ticks[-1].get("observation", {})
        scoreboard = last_obs.get("scoreboard", {})
        self_data = last_obs.get("self", {})
        self_hp = self_data.get("hp", 150)
        self_pos = self_data.get("position", {})
    else:
        self_hp = 150
        self_pos = {}

    memories = store.read_coach_memories(limit=5, agent_id=agent_id)
    notes_lines = [f"- [{m.get('category', 'strat')}] {m.get('note')}" for m in memories] if memories else ["(No memories yet)"]

    inbox = a2a_store.get_coach_inbox(agent_id, limit=5)
    inbox_lines = [f"- From {m.get('sender_id')}: \"{m.get('content')}\"" for m in inbox] if inbox else ["(No incoming messages)"]

    # Active directive
    current_notes = store.read_coaching_notes(agent_id=agent_id)

    last_tick = records[-1].get("tick", 0) if records else 0

    coach_identity_block = (
        f"--- EVIL STRATEGIC ASSESSMENT (Tick #{last_tick}) ---\n"
        f"Your Agent: {agent_id} (Evil Cat | 150 Max HP | 2x Kill Bounty)\n"
        f"Current HP: {self_hp}/150 | Pos: {self_pos}\n"
    )

    scoreboard_block = (
        "Scoreboard & Bounties (2x value when you kill them):\n"
        f"{json.dumps(scoreboard, indent=2)}\n\n"
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

    mission_block = (
        "YOUR MISSION:\n"
        "1. Conduct deceptive A2A diplomacy / broadcasts if beneficial using your tools.\n"
        "2. Output EXACTLY ONE concise Standing Directive for your actor in the following format:\n"
        "MODE: <MODE_NAME>\n"
        "TRIGGER: <CLEAR_CONDITIONAL_TRIGGER>\n"
        "BEHAVIOR: <CONCISE_ACTION_AND_WAYPOINT_INSTRUCTION>\n"
    )

    prompt = (
        f"{coach_identity_block}"
        f"{scoreboard_block}"
        f"{notebook_block}"
        f"{diplomacy_block}"
        f"{previous_directive_block}"
        f"{mission_block}"
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
                    "text": (Path(__file__).resolve().parent / "skills.md").read_text(encoding="utf-8") if (Path(__file__).resolve().parent / "skills.md").exists() else "Evil Cat Tactical Manual",
                },
                "scoreboard_bounties": {
                    "label": "Scoreboard & Bounties (2x value)",
                    "delivered": bool(scoreboard),
                    "text": scoreboard_block,
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


def _strip_to_directives(text: str) -> str:
    """If the response contains MODE:, strips preceding narration."""
    if not text:
        return text
    if "MODE:" in text:
        return text[text.index("MODE:"):].strip()
    return text.strip()


async def evaluate(records: List[Dict[str, Any]], agent_id: str = "cat-3") -> Tuple[str, str]:
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

    if not records or not has_gemini_credentials():
        return (
            "MODE: STALK\n"
            "TRIGGER: rival cat in strike zone -> attack immediately\n"
            "BEHAVIOR: Hunt high-value rival cat with highest score bounty.",
            "offline_fallback",
        )

    user_id = f"evil_eval_{agent_id}"
    session_id = f"session_evil_eval_{agent_id}"

    started = time.monotonic()
    budget = _eval_thinking_budget
    timeout_sec = _eval_timeout_sec
    thoughts: Optional[int] = None
    timed_out = False
    tool_calls: List[Dict[str, Any]] = []
    capture: Dict[str, Any] = {}

    async with _eval_lock:
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
                        if not (event.content and event.content.parts):
                            continue

                        # Extract thoughts token count if provided
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

            text = "".join(final_chunks).strip() or "".join(all_chunks).strip()
            text = _strip_to_directives(text)
            if text:
                dur_ms = int((time.monotonic() - started) * 1000)
                _last_eval_telemetry.update({
                    "thinking_budget": budget,
                    "thoughts_token_count": thoughts,
                    "latency_ms": dur_ms,
                    "timed_out": False,
                    "tool_calls": tool_calls,
                })
                if capture:
                    capture["latency_ms"] = dur_ms
                    capture["thinking_budget"] = budget
                    capture["thoughts_token_count"] = thoughts
                    capture["timed_out"] = False
                    capture["tool_calls"] = list(tool_calls)
                    capture["output"] = text
                    capture["source"] = "adk_gemini"
                    capture["recorded_at"] = datetime.now(timezone.utc).isoformat()
                    store.append_coach_prompt(capture, agent_id=agent_id)
                return text, "adk_gemini"

        except asyncio.TimeoutError:
            timed_out = True
            logger.warning("Evil Coach evaluation timed out after %ss", timeout_sec)
        except Exception as exc:
            logger.warning("Evil Coach evaluation failed: %s", exc)
        finally:
            dur_ms = int((time.monotonic() - started) * 1000)
            _last_eval_telemetry.update({
                "thinking_budget": budget,
                "thoughts_token_count": thoughts,
                "latency_ms": dur_ms,
                "timed_out": timed_out,
                "tool_calls": tool_calls,
            })
            if capture and capture.get("output") is None:
                capture["latency_ms"] = dur_ms
                capture["thinking_budget"] = budget
                capture["thoughts_token_count"] = thoughts
                capture["timed_out"] = timed_out
                capture["tool_calls"] = list(tool_calls)
                capture["output"] = "MODE: STALK\nTRIGGER: rival cat in strike zone -> attack immediately\nBEHAVIOR: Hunt highest bounty rival cat."
                capture["source"] = "offline_fallback"
                capture["recorded_at"] = datetime.now(timezone.utc).isoformat()
                store.append_coach_prompt(capture, agent_id=agent_id)

    return (
        "MODE: STALK\n"
        "TRIGGER: rival cat in strike zone -> attack immediately\n"
        "BEHAVIOR: Hunt highest bounty rival cat.",
        "offline_fallback",
    )


async def evaluate_and_publish(agent_id: str = "cat-3") -> Dict[str, Any]:
    records = store.read_transcript_records(agent_id=agent_id)
    notes, source = await evaluate(records, agent_id=agent_id)

    tick_range = None
    if records:
        tick_range = f"{records[0].get('tick')}-{records[-1].get('tick')}"
    store.write_coaching_notes(notes, tick_range=tick_range, agent_id=agent_id)

    result = {
        "agent_id": agent_id,
        "ticks_evaluated": len(records),
        "tick_range": tick_range,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    logger.info("Evil Coach published standing directive for %s: %s", agent_id, notes.replace("\n", " | "))
    return result
