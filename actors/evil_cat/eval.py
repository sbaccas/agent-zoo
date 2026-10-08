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

_eval_thinking_budget: int = EVAL_THINKING_BUDGET_DEFAULT
_eval_timeout_sec: float = EVAL_TIMEOUT_DEFAULT_SEC
_eval_lock = asyncio.Lock()


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


def build_coach_eval_prompt(records: List[Dict[str, Any]], agent_id: str = "cat-3") -> str:
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

    prompt = (
        f"--- EVIL STRATEGIC ASSESSMENT (Tick #{records[-1].get('tick', 0) if records else 0}) ---\n"
        f"Your Agent: {agent_id} (Evil Cat | 150 Max HP | 2x Kill Bounty)\n"
        f"Current HP: {self_hp}/150 | Pos: {self_pos}\n"
        f"Scoreboard & Bounties (2x value when you kill them):\n"
        f"{json.dumps(scoreboard, indent=2)}\n\n"
        f"--- YOUR PRIVATE MEMORY NOTEBOOK ---\n"
        f"{chr(10).join(notes_lines)}\n\n"
        f"--- INCOMING A2A COACH DIPLOMACY ---\n"
        f"{chr(10).join(inbox_lines)}\n\n"
        f"--- PREVIOUS STANDING DIRECTIVE ---\n"
        f"{current_notes or '(None)'}\n\n"
        f"YOUR MISSION:\n"
        f"1. Conduct deceptive A2A diplomacy / broadcasts if beneficial using your tools.\n"
        f"2. Output EXACTLY ONE concise Standing Directive for your actor in the following format:\n"
        f"MODE: <MODE_NAME>\n"
        f"TRIGGER: <CLEAR_CONDITIONAL_TRIGGER>\n"
        f"BEHAVIOR: <CONCISE_ACTION_AND_WAYPOINT_INSTRUCTION>\n"
    )
    return prompt


def _is_final_event(event: Any) -> bool:
    if not (event.content and event.content.parts):
        return False
    return not any(
        getattr(p, "function_call", None) or getattr(p, "function_response", None)
        for p in event.content.parts
    )


async def evaluate(records: List[Dict[str, Any]], agent_id: str = "cat-3") -> Tuple[str, str]:
    if not records or not has_gemini_credentials():
        return (
            "MODE: STALK\n"
            "TRIGGER: rival cat in strike zone -> attack immediately\n"
            "BEHAVIOR: Hunt high-value rival cat with highest score bounty.",
            "offline_fallback",
        )

    user_id = f"evil_eval_{agent_id}"
    session_id = f"session_evil_eval_{agent_id}"

    async with _eval_lock:
        try:
            try:
                await eval_session_service.delete_session(app_name="evil_cat_eval_app", user_id=user_id, session_id=session_id)
            except Exception:
                pass
            await eval_session_service.create_session(app_name="evil_cat_eval_app", user_id=user_id, session_id=session_id)

            prompt = build_coach_eval_prompt(records, agent_id=agent_id)
            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])

            final_chunks: List[str] = []
            all_chunks: List[str] = []

            async with asyncio.timeout(_eval_timeout_sec):
                agen = eval_runner.run_async(user_id=user_id, session_id=session_id, new_message=msg)
                try:
                    while True:
                        event = await agen.__anext__()
                        if not (event.content and event.content.parts):
                            continue
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
            if text:
                return text, "adk_gemini"

        except Exception as exc:
            logger.warning("Evil Coach evaluation failed: %s", exc)

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
