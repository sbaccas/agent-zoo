"""
Cat Evaluator — the long-horizon advisory subagent.

The Actor (``actors/cat/actor.py``) decides tick-by-tick off a short sliding window
and cannot perceive its own long-term behaviour. This subagent reads the Actor's
full transcript for the current game and writes standing orders back to it.

Handoff is deliberately file-based and one-directional:

    actor writes transcript.jsonl  ->  evaluator reads it
    evaluator writes coaching_notes.md  ->  actor reads it every tick

The API server pushes the transcript here directly on a tick interval. In
production that dispatch becomes a pub/sub publish; nothing in this module has to
change when it does, because the evaluator never reaches back into the Actor.
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from actors.auth import get_gcp_id_token
from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from actors.cat import store
from actors.cat.actor import (
    THINKING_BUDGET_AUTO,
    THINKING_BUDGET_OFF,
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

logger = logging.getLogger("cat_agent")

EVAL_MODEL = "gemini-2.5-flash"
EVAL_SKILLS_PATH = Path(__file__).parent / "eval_skills.md"

# Conversational persona for the General Radio -- the Commander's direct channel
# to the coach. Kept in its own file, and deliberately NOT merged into
# eval_skills.md, for the reason the actor keeps radio_skills.md separate: the
# advisory manual governs what the coach writes into the dossier, and a persona
# describing how to hold a conversation has no business leaking into that.
GENERAL_RADIO_SKILLS_PATH = Path(__file__).parent / "general_radio_skills.md"

# Label for the Commander's standing coaching directive inside the evaluator
# prompt. A label, not an instruction: what the coach must do with the block is
# stated in the block body itself, once, where it is written.
COACH_DIRECTIVE_BLOCK_HEADER = "--- STANDING ORDER FROM THE COMMANDER ---"

# Label for the actor manual block in the evaluator prompt.
#
# A label, not an instruction: what the coach must do with this block is stated
# once, in eval_skills.md, which refers to the block by exactly this string.
# tests/test_coach_shares_actor_model.py fails if one is renamed without the
# other -- the same arrangement actor.py uses for its own block headers.
ACTOR_MANUAL_BLOCK_HEADER = "--- YOUR SUBORDINATE'S MANUAL (actor_skills.md) ---"
CAPABILITIES_BLOCK_HEADER = "--- YOUR SUBORDINATE'S LIVE CAPABILITIES ---"

# How many ticks of play accumulate before an evaluation fires. Adjustable at
# runtime via the API server; this is only the starting value.
DEFAULT_EVAL_INTERVAL = 100

# Hard ceiling on how long a single evaluation may run. The evaluator is advisory,
# so a slow call must expire quietly rather than pile up behind the game clock.
#
# Adjustable at runtime since the thinking budget below is: the two are one
# setting wearing two hats, exactly as they are on the actor. Read through
# `get_eval_timeout()` at the point of use, never captured -- a
# `from ... import EVAL_TIMEOUT_SECONDS` elsewhere would bind a copy that never
# changes. This name remains the *default*, which is what the boxscore tool's
# timeout is sized against.
EVAL_TIMEOUT_SECONDS = 60.0

# Floor of 30s is not arbitrary: the boxscore tool below is allowed 3s, and an
# evaluation whose whole budget could be spent waiting on a wedged game server
# would publish advice derived from no measurement at all. Ten to one keeps that
# structurally impossible rather than merely unlikely; a test pins the ratio.
EVAL_TIMEOUT_MIN_SEC = 30.0
# The evaluator is off the tick loop entirely, so a long run costs latency in
# the advice rather than turns in the game. The ceiling exists only to stop a
# wedged call occupying the single evaluation slot forever.
EVAL_TIMEOUT_MAX_SEC = 900.0

# Fixed per-call overhead for one evaluation, excluding thinking. Far larger
# than the actor's 1.3s and deliberately so: this agent uploads the entire game
# transcript, the actor's manual and its own, then makes a tool call to the
# game server before it writes a word. Reusing the actor's figure here would
# have reported ~11,700 affordable thinking tokens at the default 60s when the
# real headroom is closer to 10,000.
EVAL_BASELINE_LATENCY_SEC = 10.0

# The budget this process starts the evaluator on.
#
# Default 0, preserving the behaviour every coaching note in this repo was
# written under. Kept separate from the actor's `CAT_THINKING_BUDGET` on
# purpose: the whole reason the actor's dial exists is to A/B one variable, and
# a single shared env var would move both agents at once and make the result
# uninterpretable.
EVAL_THINKING_BUDGET_ENV = "CAT_EVAL_THINKING_BUDGET"
EVAL_TIMEOUT_ENV = "CAT_EVAL_TIMEOUT_SEC"

EVAL_THINKING_MIN, EVAL_THINKING_MAX, EVAL_THINKING_CAN_BE_DISABLED = (
    thinking_bounds_for(EVAL_MODEL)
)


def coerce_eval_thinking_budget(value: Any) -> int:
    """Validates a budget against the *evaluator's* model. Raises ValueError."""
    return coerce_thinking_budget(value, model=EVAL_MODEL)


def coerce_eval_timeout(value: Any) -> float:
    """Validates an evaluation timeout in seconds, or raises ValueError."""
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"evaluation timeout must be a number, got {value!r}")
    if not (EVAL_TIMEOUT_MIN_SEC <= timeout <= EVAL_TIMEOUT_MAX_SEC):
        raise ValueError(
            f"evaluation timeout {timeout:g}s is out of range: use "
            f"{EVAL_TIMEOUT_MIN_SEC:g}-{EVAL_TIMEOUT_MAX_SEC:g} seconds"
        )
    return timeout


def _initial_from_env(env_name: str, coerce, default, describe) -> Any:
    """Reads a startup setting from the environment, warning and falling back.

    A malformed value is a warning and a fallback, not a crash: the workbench
    failing to boot over a typo in a tuning variable would be worse than
    starting in the configuration everything was measured under. The value is
    echoed either way, because a silently ignored setting is how you end up
    comparing two runs that were actually identical.
    """
    raw = os.environ.get(env_name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = coerce(raw)
    except ValueError as exc:
        print(
            f"⚠️  [EVAL] Ignoring {env_name}={raw!r}: {exc}. Using {describe(default)}.",
            flush=True,
        )
        return default
    print(f"🧠 [EVAL] {env_name}: {describe(value)}", flush=True)
    return value


_eval_timeout_sec: float = _initial_from_env(
    EVAL_TIMEOUT_ENV, coerce_eval_timeout, EVAL_TIMEOUT_SECONDS, lambda v: f"{v:g}s"
)


def get_eval_timeout() -> float:
    """Seconds one evaluation may run before it is abandoned."""
    return _eval_timeout_sec


def set_eval_timeout(value: Any) -> float:
    """Changes the abandon ceiling. Applies to the next evaluation, not a running one."""
    global _eval_timeout_sec
    _eval_timeout_sec = coerce_eval_timeout(value)
    return _eval_timeout_sec


# The Commander's standing order to the coach, set over the General Radio.
#
# Held here rather than on the API's `state` object because it is an input to
# the evaluation prompt, and every other runtime-adjustable evaluation input --
# the thinking budget, the timeout -- already lives in this module behind a
# get/set pair. Keeping them together is what lets the archive stamp the
# complete coaching configuration from one place.
#
# Deliberately a separate slot from the published dossier in coaching_notes.md.
# The coach rewrites that file wholesale on every cycle, so an order stored
# there would survive exactly one evaluation before the coach overwrote it --
# the same bug that made the actor's commander orders evaporate, which is why
# `state.commander_directive` got its own slot too.
_coach_directive: Optional[Dict[str, Any]] = None


def get_coach_directive() -> Optional[Dict[str, Any]]:
    """The Commander's standing order to the coach, or None if none is in force."""
    return _coach_directive


def set_coach_directive(text: str, tick: Optional[int] = None) -> Dict[str, Any]:
    """Records a standing order. Applies from the next evaluation onward."""
    global _coach_directive
    _coach_directive = {
        "directive": text.strip(),
        "tick": tick,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "source": "general_radio",
    }
    return _coach_directive


def clear_coach_directive() -> bool:
    """Rescinds the standing order. Returns whether one was actually in force."""
    global _coach_directive
    had = _coach_directive is not None
    _coach_directive = None
    return had


def get_eval_instruction() -> str:
    """Loads the evaluator's own skills manual, distinct from the actor's."""
    if EVAL_SKILLS_PATH.exists():
        return EVAL_SKILLS_PATH.read_text(encoding="utf-8")
    return (
        "You are the Cat Evaluator, a long-horizon advisor to a Cat agent hunting "
        "mice on a discrete grid. Read the transcript and write short, concrete, "
        "directive markdown bullets advising the Cat what to do next."
    )


def _eval_instruction_provider(_ctx: ReadonlyContext) -> str:
    """Re-reads the manual on every run so edits take effect immediately.

    This used to be `instruction=get_eval_instruction()`, evaluated once when the
    module was imported. Because uvicorn's --reload only watches .py files, an
    edit to eval_skills.md changed nothing until some unrelated Python file
    happened to be touched. Worse, GET /api/agent/evaluator/skills reads the file
    directly, so the workbench displayed the new rules while the agent kept
    obeying the old ones -- the file looked applied when it was not.
    """
    return get_eval_instruction()


def get_general_radio_instruction() -> str:
    """Persona for free-form General radio replies, read fresh from disk."""
    if not GENERAL_RADIO_SKILLS_PATH.exists():
        raise FileNotFoundError(
            f"General radio persona missing at {GENERAL_RADIO_SKILLS_PATH}."
        )
    return GENERAL_RADIO_SKILLS_PATH.read_text(encoding="utf-8")


def _general_instruction_provider(_ctx: ReadonlyContext) -> str:
    """The evaluator's own manual, plus the radio persona. Both read fresh.

    The coach's manual leads for the same reason it does on the evaluation path:
    the Coach answering the General's radio and the evaluator writing the
    dossier must be the same officer. If the radio agent ran on the persona
    alone it would happily defend directives using a model of the game it had
    invented on the spot, which is the exact failure eval_skills.md section 1
    exists to prevent.
    """
    return get_eval_instruction() + "\n\n" + get_general_radio_instruction()


# ---------------------------------------------------------------------------
# Box score tool
#
# The coach reads the transcript, which is a record of what the Cat chose. It
# carries no record of what those choices were worth, and no record at all of
# the heuristic cat-2 the Cat is losing to -- cat-2 is not driven by this
# workbench and never writes a transcript. So the coach could observe "you
# rarely lunge" but not "your lunge share is 10.4% against the rival's 13.0%",
# and it could not tell whether last evaluation's directive on that KPI moved
# it. The result was the same qualitative correction reissued game after game.
#
# The engine already measures all of this for every cat (app/game/stats.py).
# This tool is the read path onto it. It returns numbers and plain labels only:
# every imperative about when to call it, what counts as bad, and what to do
# about it lives in eval_skills.md, per the rule in
# tests/test_prompt_is_data_only.py.
# ---------------------------------------------------------------------------

# The game server is a separate process from the workbench that runs the coach,
# so its address is configuration, not a constant. Overridable for the same
# reason CAT_AGENT_RUNTIME_DIR is: a test run must not reach a live server.
GAME_SERVER_URL_ENV = "CAT_AGENT_GAME_SERVER_URL"
DEFAULT_GAME_SERVER_URL = "http://127.0.0.1:8000"

# "0" switches the fetch off, following CAT_AGENT_AUTO_CONNECT's convention.
# Under pytest the default flips: the suite must not dial :8000, where a live
# game would make every assertion depend on whatever happens to be playing.
# Set it to "1" to opt a test back in deliberately.
BOXSCORE_FETCH_ENV = "CAT_AGENT_BOXSCORE_FETCH"

# The coach is advisory and runs on the game's clock. A wedged or absent game
# server has to cost a couple of seconds, not the whole evaluation budget --
# EVAL_TIMEOUT_SECONDS above is the ceiling this has to fit well inside.
BOXSCORE_TIMEOUT_SECONDS = 3.0

# The coach only ever evaluates the game in progress; the server resolves this
# literal to whichever simulation that is.
BOXSCORE_SIM_ID = "active"


def _boxscore_fetch_enabled() -> bool:
    """Whether the tool may open a socket, given the environment."""
    setting = os.environ.get(BOXSCORE_FETCH_ENV)
    if setting is not None:
        return setting != "0"
    return "PYTEST_CURRENT_TEST" not in os.environ


def _boxscore_unavailable(reason: str) -> Dict[str, Any]:
    """The one shape returned when no measurement could be obtained.

    Explicitly empty rather than zero-filled. A row of zeros is indistinguishable
    from a cat that did nothing, and the coach would report it as fact.
    """
    return {"available": False, "reason": reason}


async def get_boxscore() -> Dict[str, Any]:
    """Reads the game engine's box score for the game currently in progress.

    The engine measures these counters itself, independently of the transcript,
    and covers every cat on the board -- both the cat being coached and its
    rival.

    Returns a dict. When the measurement was obtained:

      available: true
      simulation_id, status, tick, round: identity and clock of the game
      tracking_since_tick: the first tick these counters cover
      ticks_measured: tick minus tracking_since_tick
      arena: width, height, tiles
      leader: id of the cat with the most kills, or null
      mice_alive, mice_initial, captures: population counts (when disclosed)
      cats: one row per cat, each with
        id, mode, alive, position (x, y)
        kills, kills_per_100_ticks, tiles_per_kill
        slash_attempts, slash_accuracy, whiffs, multi_kills
        zone_chances, zone_conversion, zone_declined
        tiles_run, unique_tiles, ground_covered, speed_tiles_per_tick,
        ticks_per_tile
        moves, lunges, lunge_share, wall_bonks
        ticks_free, ticks_locked, locked_pct, prey_in_sight_pct

    Rate fields are fractions in 0..1, not percentages. A rate whose denominator
    is zero is null rather than 0.0, so nine of them are null on a game that has
    only just begun.

    When the game server cannot be reached, returns available: false with a
    reason string and no statistics.
    """
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
            return res
        if isinstance(res, dict) and res.get("available") is False:
            return res
        return _boxscore_unavailable("MCP server returned unexpected response")
    except Exception as exc:
        logger.warning("MCP boxscore fetch failed: %s: %s", type(exc).__name__, exc)
        return _boxscore_unavailable(f"MCP server unavailable at {mcp_url}: {type(exc).__name__}: {exc}")


def _coverage_map_unavailable(reason: str) -> Dict[str, Any]:
    """The one shape returned when coverage map could not be obtained."""
    return {"available": False, "reason": reason}


async def get_coverage_map() -> Dict[str, Any]:
    """Inspects the spatial grid heatmap showing where cats have spent their ticks across the arena via MCP.

    The coach accesses this exclusively through the Model Context Protocol (MCP) server.
    """
    if not _boxscore_fetch_enabled():
        return _coverage_map_unavailable(
            f"coverage map fetch is switched off ({BOXSCORE_FETCH_ENV}=0)"
        )

    mcp_url = (
        os.environ.get("CAT_MCP_SERVER_URL")
        or os.environ.get("MCP_SERVER_URL")
        or "http://127.0.0.1:9050"
    )

    try:
        from app.mcp.client import MCPClient
        client = MCPClient(base_url=mcp_url)
        res = await client.call_tool("get_coverage_map", {"sim_id": BOXSCORE_SIM_ID})
        if isinstance(res, dict) and (res.get("available") is not False or "ascii" in res):
            return res
        if isinstance(res, dict) and res.get("available") is False:
            return res
        return _coverage_map_unavailable("MCP server returned unexpected response")
    except Exception as exc:
        logger.warning("MCP coverage map fetch failed: %s: %s", type(exc).__name__, exc)
        return _coverage_map_unavailable(f"MCP server unavailable at {mcp_url}: {type(exc).__name__}: {exc}")


def _events_unavailable(reason: str) -> Dict[str, Any]:
    """The shape returned when game events could not be obtained."""
    return {"available": False, "reason": reason}


async def get_event_reel(
    limit: Optional[int] = 20,
    since_tick: Optional[int] = None,
    actor_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Reads granular combat events, whiffs, attacks, and deaths for the game in progress.

    Returns a dict with:
      available: true
      simulation_id, tick, round
      events: list of events, each with tick, event_type, actor_id, target_id, details
      deaths: list of death records
      entity_events: dictionary mapping entity ids to their specific event lists
    """
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
                return _events_unavailable("events disclosure is disabled")
            if resp.status_code != 200:
                return _events_unavailable(f"game server returned HTTP {resp.status_code}")
            payload = resp.json()
    except Exception as exc:
        logger.warning("Event reel fetch failed: %s: %s", type(exc).__name__, exc)
        return _events_unavailable(f"{type(exc).__name__}: {exc}")

    if not isinstance(payload, dict):
        return _events_unavailable("game server returned an unexpected payload")

    return {
        "available": True,
        "simulation_id": payload.get("simulation_id"),
        "tick": payload.get("tick"),
        "round": payload.get("round"),
        "events": payload.get("events", []),
        "deaths": payload.get("deaths", []),
        "entity_events": payload.get("entity_events", {}),
    }


async def write_coach_note(
    note: str,
    category: str = "tactical",
    tags: Optional[List[str]] = None,
    linked_ticks: Optional[List[int]] = None,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Records a private entry to the Coach's tactical notebook (coach_memory.jsonl).

    Call this during evaluation to log key hypotheses, spatial deductions, mouse clustering,
    or observations for your future self across cycles. You can pass linked_ticks=[...] to attach
    specific transcript ticks and their snapshots for memory retrieval. Note: this is separate
    from the actor's published standing orders in coaching_notes.md.
    """
    target_id = cat_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    mcp_url = os.environ.get("CAT_MCP_SERVER_URL") or os.environ.get("MCP_SERVER_URL") or "http://127.0.0.1:9050"
    if mcp_url:
        try:
            from app.mcp.client import MCPClient
            client = MCPClient(base_url=mcp_url)
            res = await client.call_tool(
                "write_coach_note",
                {"note": note, "category": category, "tags": tags, "linked_ticks": linked_ticks, "cat_id": target_id},
            )
            if res.get("status") == "saved":
                return res
        except Exception:
            pass

    record = store.append_coach_memory(
        note=note, category=category, tags=tags, linked_ticks=linked_ticks, agent_id=target_id
    )
    return {"status": "saved", "memory": record}


async def read_coach_notes(
    limit: Optional[int] = 10,
    category: Optional[str] = None,
    search: Optional[str] = None,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Searches and retrieves historical entries from the Coach's tactical notebook across evaluation cycles."""
    target_id = cat_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    mcp_url = os.environ.get("CAT_MCP_SERVER_URL") or os.environ.get("MCP_SERVER_URL") or "http://127.0.0.1:9050"
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
    """Pulls recent or windowed Cat agent transcript records (positions, facing, visibility, moves, reasoning) on demand.

    Deliberately omits absolute tick numbers to prevent the Coach from citing raw tick indices back to the Actor.
    """
    target_id = cat_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
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
    recipient_coach: str,
    message: str,
    message_type: str = "DIRECT_MESSAGE",
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Sends an Agent-to-Agent (A2A) strategic message from this Coach (Long-Term Consciousness) to a peer Coach.

    Use this tool to communicate directly with the other team's Coach (e.g. 'coach(cat-2)').
    Ideal for strategic diplomacy: proposing territorial division, non-aggression pacts,
    or coordinated hunting boundaries.

    Args:
        recipient_coach: Target coach identifier (e.g. 'coach(cat-2)' or 'coach(cat-1)').
        message: The strategic proposition or message content.
        message_type: 'DIRECT_MESSAGE', 'PROPOSAL', or 'PACT'. Default is 'DIRECT_MESSAGE'.
        cat_id: Optional explicit sender cat id (defaults to current active cat).

    Returns:
        Status dictionary confirming dispatch and message ID.
    """
    target_cat = cat_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    sender_coach_id = f"coach({target_cat})"

    recip = recipient_coach.strip()
    if recip not in ("*", "broadcast", "all") and not recip.startswith("coach"):
        recip = f"coach({recip})"

    try:
        from actors.a2a import client
        resp = await client.send_a2a_message(
            sender_id=sender_coach_id,
            recipient_id=recip,
            content=message,
            message_type=message_type,
            metadata={"source": "long_term_consciousness", "layer": "coach"},
        )
        return {
            "status": resp.status,
            "message_id": resp.message_id,
            "sender": sender_coach_id,
            "recipient": recip,
            "message": message,
            "error": resp.error,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "sender": sender_coach_id, "recipient": recip}


async def a2a_read_coach_messages(
    limit: Optional[int] = 10,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reads incoming A2A strategic messages and proposals sent by peer Coaches.

    Returns the latest incoming diplomatic messages from other coaches on the board.
    """
    target_cat = cat_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    from actors.a2a import store as a2a_store
    coach_msgs = a2a_store.get_coach_inbox(target_cat, limit=limit or 10)
    formatted = [
        {
            "sender": m.get("sender_id"),
            "recipient": m.get("recipient_id"),
            "type": m.get("message_type"),
            "content": m.get("content"),
            "timestamp": m.get("timestamp"),
        }
        for m in coach_msgs
    ]
    return {"available": True, "count": len(formatted), "messages": formatted}


async def a2a_get_peer_coach_card(
    peer_id: Optional[str] = None,
    cat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspects the peer agent's official A2A Agent Card to check its declared long-term consciousness and strategic skills."""
    target_cat = cat_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    peer = peer_id or ("cat-1" if target_cat == "cat-2" else "cat-2")
    try:
        from actors.a2a import client
        card = await client.fetch_agent_card(agent_id=peer)
        if card:
            return {"status": "ok", "agent_card": card.model_dump()}
        return {"status": "error", "error": f"Could not retrieve card for peer {peer}"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# The budget the coach starts on, and it is deliberately ~16x the actor's.
#
# The two agents are the same model doing opposite jobs, and the shape of the
# job is what sets the number:
#
#   - The actor answers one question about one board, four times a second, and
#     every token it spends thinking is latency against a tick that is already
#     moving. Its constraint is the clock.
#   - The coach runs once per evaluation cycle, off the tick loop entirely, and
#     has to read a whole transcript, call `get_boxscore`, reconcile what it
#     sees against directives it wrote earlier, and decide what to retract. Its
#     constraint is the size of the reasoning, and nothing downstream is waiting
#     on it.
#
# 4,096 sits well inside the 60s default timeout (which affords ~10,000 tokens),
# so raising the budget to this does not bring the abandon ceiling into play.
# That matters more here than on the actor side: an evaluator timeout does not
# merely skip a turn, it publishes `_summarise_without_model` positional filler
# *over* the standing orders the Cat is reading every tick.
EVAL_THINKING_BUDGET_DEFAULT = 4096

_initial_eval_budget: int = _initial_from_env(
    EVAL_THINKING_BUDGET_ENV,
    coerce_eval_thinking_budget,
    EVAL_THINKING_BUDGET_DEFAULT,
    describe_thinking_budget,
)

# The thinking budget here is a *starting* value. It is rebound at runtime by
# `set_eval_thinking_budget` and must therefore never be read back from this
# literal anywhere else -- `get_eval_thinking_budget` reads the live agent, so
# the UI, the archive and the model cannot disagree about what is in force.
eval_agent = LlmAgent(
    name="cat_evaluator",
    model=EVAL_MODEL,
    instruction=_eval_instruction_provider,
    tools=[
        get_boxscore,
        get_coverage_map,
        get_event_reel,
        pull_transcript,
        write_coach_note,
        read_coach_notes,
        a2a_send_coach_message,
        a2a_read_coach_messages,
        a2a_get_peer_coach_card,
    ],
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=_initial_eval_budget)
    ),
)
eval_session_service = InMemorySessionService()
eval_runner = Runner(
    agent=eval_agent,
    app_name="cat_evaluator_app",
    session_service=eval_session_service,
)

_eval_lock = asyncio.Lock()

# The General Radio agent -- the Commander's direct channel to the coach.
#
# Shares `eval_session_service` with the evaluator so the two are cheap to hold,
# but runs on its own session id, so a radio conversation can never be mistaken
# for evaluation context and vice versa.
#
# Deliberately NOT wired to `set_eval_thinking_budget`, for the same reason the
# actor's interrogator is held out of the actor's dial: this agent answers the
# human, it is off the evaluation cycle, its latency costs the Cat nothing, and
# including it would mean an A/B on the coach's budget silently changed the
# radio's behaviour too. An experiment about coaching quality should measure the
# thing that writes the coaching.
#
# It carries `get_boxscore` because the persona tells it to fetch numbers rather
# than quote them from memory -- without the tool that instruction is unfollowable
# and the agent would invent statistics, which is the single worst thing this
# channel could do.
general_agent = LlmAgent(
    name="cat_coach",
    model=EVAL_MODEL,
    instruction=_general_instruction_provider,
    tools=[
        get_boxscore,
        get_event_reel,
        pull_transcript,
        read_coach_notes,
        a2a_read_coach_messages,
        a2a_send_coach_message,
    ],
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    ),
)
general_runner = Runner(
    agent=general_agent,
    app_name="cat_evaluator_app",
    session_service=eval_session_service,
)

# Its own lock. Sharing `_eval_lock` would have meant a radio call blocking
# behind a running evaluation -- up to the full evaluation timeout, which is
# minutes at the top of its range -- and the Commander would have read that as
# the channel being dead.
_general_lock = asyncio.Lock()

# How long a radio reply may take.
#
# Strictly below EVAL_TIMEOUT_MIN_SEC, and a test pins that. The two ceilings
# are answering different questions: an evaluation is a background job whose
# only cost is staleness, while this one has a human sitting in front of it. It
# was briefly set equal to the evaluation floor, which satisfied neither reading.
#
# The budget: get_boxscore is capped at 3s, the agent thinks at budget 0, and a
# 2.5-flash turn with one tool call lands well inside 10s. 20s leaves room for a
# slow fetch and a long answer without ever making the Commander wonder whether
# the channel is dead.
GENERAL_RADIO_TIMEOUT_SECONDS = 20.0



def get_eval_thinking_budget() -> Optional[int]:
    """The budget the evaluator is running with right now, off the live agent."""
    cfg = getattr(eval_agent, "generate_content_config", None)
    thinking = getattr(cfg, "thinking_config", None)
    return getattr(thinking, "thinking_budget", None)


def set_eval_thinking_budget(value: Any) -> int:
    """Rebinds the evaluator's thinking budget. Applies to the next evaluation.

    A *new* ThinkingConfig is assigned rather than the existing one mutated, for
    the same reason the actor does it: ADK's per-request copy of
    `generate_content_config` is shallow, so an evaluation already in flight
    holds a reference to the very object a mutation would edit. Swapping the
    object leaves a running evaluation on the config it started under, which is
    what makes the published notes attributable to a budget at all.

    Returns the budget now in force. Raises ValueError if the model would not
    accept it.
    """
    budget = coerce_eval_thinking_budget(value)
    cfg = eval_agent.generate_content_config
    if cfg is None:
        eval_agent.generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=budget)
        )
    else:
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=budget)
    return budget


def eval_affordable_thinking_tokens(timeout_sec: Optional[float] = None) -> int:
    """Roughly how many thinking tokens fit inside the evaluation ceiling."""
    if timeout_sec is None:
        timeout_sec = get_eval_timeout()
    return estimate_affordable_tokens(timeout_sec, EVAL_BASELINE_LATENCY_SEC)


def suggested_eval_timeout(budget: Optional[int]) -> Optional[float]:
    """The timeout a given budget would need, or None if the current one fits."""
    tokens = nominal_thinking_tokens(budget)
    if tokens is None or tokens <= eval_affordable_thinking_tokens():
        return None
    return estimate_required_timeout(
        tokens, EVAL_BASELINE_LATENCY_SEC, EVAL_TIMEOUT_MAX_SEC
    )


def eval_thinking_warning(budget: Optional[int]) -> Optional[str]:
    """Flags budgets the evaluation ceiling cannot afford, or None if fine.

    The failure mode here is quieter than the actor's and worth spelling out.
    An evaluation that overruns is abandoned and replaced by
    `_summarise_without_model`, which emits positional filler about bounding
    boxes. That filler is then published to `coaching_notes.md` and injected
    into the actor's prompt on every subsequent tick, displacing whatever real
    advice was there before. So an over-ambitious budget does not merely fail to
    help -- it actively degrades the actor by overwriting good standing orders
    with generated boilerplate.
    """
    tokens = nominal_thinking_tokens(budget)
    if tokens is None:
        return None

    timeout_sec = get_eval_timeout()
    affordable = eval_affordable_thinking_tokens(timeout_sec)
    suggested = suggested_eval_timeout(budget)
    if suggested is None:
        return None

    subject = (
        f"Auto can spend up to ~{tokens:,} thinking tokens, which"
        if budget == THINKING_BUDGET_AUTO
        else f"{budget:,} thinking tokens is"
    )
    return (
        f"{subject} unlikely to fit inside the {timeout_sec:g}s evaluation "
        f"timeout (~{affordable:,} tokens of headroom once the transcript "
        f"upload and boxscore call are paid for). An evaluation that overruns "
        f"is abandoned and the offline fallback publishes positional filler "
        f"over your standing orders. Raise the timeout to about {suggested:g}s "
        f"to give this budget room to land."
    )


# Telemetry from the most recent evaluation, for the UI and the archive.
#
# Held here rather than returned from `evaluate()` so the function keeps its
# two-value contract, and so the workbench can still show what the last run
# spent during the long gaps between publishes. `thoughts_token_count` is left
# as None when the response carried no usage metadata, which is deliberately
# distinct from 0: one means "not reported", the other means "did not think".
_last_eval_telemetry: Dict[str, Any] = {
    "thinking_budget": None,
    "thoughts_token_count": None,
    "latency_ms": None,
    "timed_out": False,
    "tool_calls": [],
}


def get_last_eval_telemetry() -> Dict[str, Any]:
    """What the most recent evaluation cost. Copied so callers cannot mutate it."""
    return dict(_last_eval_telemetry)


def format_transcript(records: List[Dict[str, Any]]) -> str:
    """Renders transcript records as readable lines for the model.

    Deliberately emits NO absolute tick index.

    The Actor only ever sees a ~25-tick sliding window, so a directive citing
    "tick 26" refers to something it cannot look up; those citations are pure
    noise in its context. Telling the evaluator not to cite ticks helped but did
    not eliminate it -- the model kept reaching for the numbers because every
    line it read was labelled with one. Not showing it an index is deterministic
    where an instruction is merely probable.

    Ordering is preserved by line order, so runs, oscillations and durations
    remain fully analysable ("roughly 40 attacks", "for the last third").
    """
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
        # A "cooldown" row is a tick the cat was locked: it observed the board but
        # the engine discarded whatever it returned. Rendering these the same as
        # real moves made long runs of rejected commands look like the cat
        # stubbornly repeating itself, and the coach then scolded it for a choice
        # it never got to make.
        if r.get("tick_type") == "cooldown" or r.get("executed") is False:
            did = f"LOCKED (recovering from {r.get('locked_action') or 'previous action'}; no action possible)"
        else:
            did = f"did {action}"
        lines.append(
            f"at ({r.get('x')},{r.get('y')}) facing "
            f"{r.get('facing')} | score {r.get('score', 0)} | saw {seen} | {did}"
            + (f' | reasoning: "{thought}"' if thought else "")
        )
    return "\n".join(lines)


def _summarise_without_model(records: List[Dict[str, Any]]) -> str:
    """Offline advice used when no Gemini credentials are available.

    This keeps the reflexion loop observable during tests and local runs without
    credentials. It is intentionally thin: real advice is the model's job.
    """
    if not records:
        return "- **No Data**: No ticks recorded yet this game. Begin a broad sweep of the arena."

    xs = [r.get("x") for r in records if isinstance(r.get("x"), int)]
    ys = [r.get("y") for r in records if isinstance(r.get("y"), int)]
    cells = {(r.get("x"), r.get("y")) for r in records}
    facings = {r.get("facing") for r in records if r.get("facing")}

    bullets = [
        f"- **Coverage So Far**: Across {len(records)} ticks you occupied "
        f"{len(cells)} distinct cells."
    ]
    if xs and ys:
        bullets.append(
            f"- **Bounding Box**: Your movement stayed within x=[{min(xs)},{max(xs)}], "
            f"y=[{min(ys)},{max(ys)}]. Push beyond this box to reveal new ground."
        )
    unfaced = {"north", "south", "east", "west"} - facings
    if unfaced:
        bullets.append(
            f"- **Blind Directions**: You never faced {', '.join(sorted(unfaced))}. "
            "Your viewport is forward-only, so those bearings are unobserved."
        )
    bullets.append(
        "- **Note**: Generated without model credentials; directives are positional only."
    )
    return "\n".join(bullets)


def format_capabilities(records: List[Dict[str, Any]]) -> str:
    """Renders the Cat's live tunables, plus the strike zone they imply.

    The manual is deliberately written so that every number in it is an
    illustration and the live `capabilities` block is the only authority. The
    Actor gets that block on every observation. The evaluator never did, so the
    one document it had to reason from told it, correctly, not to trust any of
    its own printed figures -- and gave it nothing to use instead.

    The strike zone is spelled out as an explicit predicate rather than left for
    the model to derive. Its depth is the single value the coaching notes have
    actually got wrong in play, and a derived line cannot drift from the config
    the way a sentence of prose can.
    """
    seen: List[Dict[str, Any]] = []
    for r in records:
        caps = r.get("capabilities") or {}
        if caps and (not seen or caps != seen[-1]):
            seen.append(caps)
    if not seen:
        return ""

    latest = seen[-1]
    lines = [f"  {k}: {v}" for k, v in sorted(latest.items())]

    zone = ""
    reach = latest.get("attack_range")
    if isinstance(reach, int):
        zone = (
            f"\nStrike zone implied by attack_range={reach}: a rectangle "
            f"{reach} deep and 3 wide. A mouse is hittable if and only if\n"
            f"  1 <= forward <= {reach}   AND   -1 <= offaxis <= 1\n"
            "Anything outside that is a guaranteed miss and a wasted "
            "attack_cooldown. Note the width: a mouse one tile off the centre "
            "line IS in the zone. Do not tell the Cat to align to an exact row "
            "or column -- that discards two thirds of its reach.\n"
        )

    # The viewport, spelled out the same way as the strike zone. Without this
    # the evaluator sees three unexplained integers and has no way to tell a
    # "should have taken that shot" from a "could not see it", which are
    # opposite coaching notes. Derived rather than hardcoded because all three
    # dimensions are operator-tunable at runtime.
    viewport = ""
    v_fwd = latest.get("vision_radius")
    v_rear = latest.get("vision_rear")
    v_half = latest.get("vision_half_width")
    if all(isinstance(v, int) for v in (v_fwd, v_rear, v_half)):
        viewport = (
            f"\nViewport implied by those vision values: a box around the Cat, "
            f"{v_fwd + v_rear + 1} deep and {2 * v_half + 1} wide. It sees a "
            f"mouse if and only if\n"
            f"  {-v_rear} <= forward <= {v_fwd}   AND   {-v_half} <= offaxis <= {v_half}\n"
            "Anything outside that the Cat genuinely could not see, so never "
            "fault it for missing one. The box is wider than the strike zone, "
            "so most mice it can see are ones it cannot hit from where it "
            "stands -- holding fire on those is correct play, not a lost "
            "chance. Only fault it for passing up a mouse that was inside the "
            "strike zone above.\n"
        )

    retuned = ""
    if len(seen) > 1:
        retuned = (
            "\nNOTE: these were retuned during this stretch of play. The values "
            "above are the most recent, and are the ones your directives must "
            "assume.\n"
        )

    return (
        f"{CAPABILITIES_BLOCK_HEADER}\n"
        + "\n".join(lines)
        + "\n"
        + zone
        + viewport
        + retuned
        + "\nThese are the live values from the game server and they override "
        "every illustrative figure printed in the manual above.\n\n"
    )


def build_eval_prompt(
    records: List[Dict[str, Any]],
    previous: str,
    coach_directive: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """Assembles the full evaluator prompt.

    Extracted from evaluate() purely so it can be tested. While this was inline,
    mutation testing showed the tick range could be put back into the header
    with the entire suite still green: the tests could only reach
    format_transcript, and the header is the other route by which an absolute
    tick index reaches the model.

    The actor's manual leads the prompt. It is static across every evaluation
    and the transcript is not, so putting it first is also the order that lets a
    provider cache the long prefix.
    """
    target_cat = agent_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    coach_identity_block = (
        f"--- YOUR ASSIGNMENT: DEDICATED COACH FOR {target_cat.upper()} (LONG-TERM CONSCIOUSNESS) ---\n"
        f"You are the dedicated tactical coach and long-term consciousness exclusively assigned to {target_cat.upper()} (identity: coach({target_cat})).\n"
        f"Your role is macro strategy, long-horizon evaluation, and strategic diplomacy with peer coaches.\n"
        f"Your subordinate is the Tactical Cat Actor ({target_cat}), who acts as the short-term consciousness executing immediate movement and combat.\n"
        f"Do NOT confuse your assigned agent ({target_cat.upper()}) with any other cat or agent on the arena board.\n\n"
    )

    # The coach's model of the game IS the actor's manual -- the same bytes the
    # actor is running on, read through the actor's own loader so the two cannot
    # diverge. eval_skills.md used to carry its own paraphrase of the rules and
    # it rotted: it described the strike zone as an "expanding 60 degree cone"
    # long after the engine, the heuristic and actor_skills.md had settled on a
    # 3-wide rectangle, and its worked examples told the coach to align the cat
    # to the mouse's "exact row or column". That advice reached the cat verbatim
    # and cost it most of a game: swings that were correctly aligned were being
    # "corrected" while the real error, depth, went unmentioned.
    actor_manual_block = (
        f"{ACTOR_MANUAL_BLOCK_HEADER}\n"
        f"{get_skills_instruction()}\n\n"
        f"--- END OF YOUR SUBORDINATE'S MANUAL ---\n\n"
    )

    previous_block = ""
    if previous:
        previous_count = sum(
            1 for ln in previous.splitlines() if ln.strip().startswith("-")
        )
        # The wording here governs how the list evolves. It used to offer
        # only "keep / drop / replace" -- three operations that all
        # preserve cardinality -- so the list could never grow. Measured
        # live: feeding in 2 bullets produced 2-3 back, 6 produced 5.
        # Output tracked input almost exactly. Adding must be named as an
        # explicit, expected operation or it does not happen.
        previous_block = (
            "--- YOUR STANDING DIRECTIVES SO FAR ---\n"
            f"{previous}\n\n"
            "This is your accumulated dossier, not a draft to be rewritten. "
            "Assess whether the Cat actually followed each directive, then "
            "produce the updated dossier:\n"
            "  - CORRECT, before anything else, any directive that contradicts "
            "your subordinate's manual or its live capabilities above. Because "
            "the default is to carry directives forward verbatim and the Cat "
            "obeys them, a wrong directive does not decay -- it persists and "
            "keeps costing kills until you repair it. Rewrite it to agree with "
            "the capabilities block.\n"
            "  - CARRY FORWARD every directive that still holds, reproduced "
            "verbatim. This is the default; most directives should survive.\n"
            "  - DROP only a directive the Cat has demonstrably completed or "
            "that the transcript has proven wrong.\n"
            "  - SHARPEN a directive the Cat ignored, making it more concrete "
            "and easier to act on.\n"
            "  - ADD new directives for everything this stretch of transcript "
            "revealed that your dossier does not yet cover.\n\n"
            f"You currently have {previous_count} directive(s). The dossier is "
            "expected to grow as the game progresses and you learn more; a "
            "later evaluation covering more of the game should normally return "
            "more directives than an earlier one. Do not shrink it to hit some "
            "imagined length, and do not pad it to look thorough.\n\n"
        )

    # The Commander's standing order, if one is in force.
    #
    # Placed AFTER the dossier and the transcript, immediately before the write
    # instruction, because it has to win. The dossier block above tells the coach
    # that carrying directives forward verbatim is the default, and that default
    # is strong enough to survive a contradicting instruction buried earlier in a
    # prompt this long -- which would leave the Commander watching an order they
    # gave get quietly overwritten by the coach's own previous opinion, every
    # cycle, with no error anywhere.
    directive_block = ""
    if coach_directive and coach_directive.strip():
        directive_block = (
            f"{COACH_DIRECTIVE_BLOCK_HEADER}\n"
            f"{coach_directive.strip()}\n\n"
            "This order comes from the human Commander, who outranks you. It "
            "applies to the dossier you are about to write and to every dossier "
            "after it, until the Commander rescinds it.\n"
            "  - It overrides your own standing directives where they conflict. "
            "If a directive already in your dossier contradicts this order, "
            "rewrite or drop that directive now rather than carrying it "
            "forward.\n"
            "  - It does not override your subordinate's manual. If the order "
            "would have you tell the Cat to do something illegal or impossible "
            "under that manual, do not issue it. Say so plainly in a directive "
            "instead, and explain the conflict.\n\n"
        )

    memories = store.read_coach_memories(limit=5, agent_id=target_cat)
    notebook_block = ""
    if memories:
        notes_lines = []
        for m in memories:
            tags = f" [tags: {', '.join(m['tags'])}]" if m.get("tags") else ""
            cat = f"[{m.get('category', 'tactical')}]"
            notes_lines.append(f"  • {cat}{tags} {m.get('note', '')}")
        notebook_block = (
            "--- YOUR PRIVATE STRATEGIC NOTEBOOK (from earlier rounds) ---\n"
            + "\n".join(notes_lines)
            + "\n(Use write_coach_note to log new deductions or hypotheses for future rounds)\n\n"
        )
    else:
        notebook_block = (
            "--- YOUR PRIVATE STRATEGIC NOTEBOOK ---\n"
            "(Notebook is currently empty. Use write_coach_note during evaluation to record private tactical deductions, hypotheses, and sector patterns across rounds.)\n\n"
        )

    # No tick indices reach the model -- not on the lines, not in this
    # header. See format_transcript() for why.
    # The full transcript is not dumped into the default prompt context to avoid
    # blowing up the token budget. The model queries recent or windowed slices
    # on demand via pull_transcript.
    transcript_block = (
        f"--- CAT TRANSCRIPT STATUS ({len(records)} consecutive ticks recorded) ---\n"
        "The raw transcript is no longer dumped into your prompt context. "
        "Use the pull_transcript tool on demand to inspect recent movement, combat, "
        "chosen actions, and reasoning (e.g. pull_transcript(last_n=50)).\n\n"
    )

    # A2A Strategic Peer Comms block
    from actors.a2a import store as a2a_store
    coach_comms = a2a_store.get_coach_inbox(target_cat, limit=10)
    if coach_comms:
        comms_lines = []
        for m in coach_comms:
            sender = m.get("sender_id", "peer_coach")
            m_type = m.get("message_type", "MSG")
            content = m.get("content", "")
            comms_lines.append(f"  • [{m_type} from {sender}]: \"{content}\"")
        comms_block = (
            "--- A2A STRATEGIC DIPLOMACY (PEER COACH COMMS) ---\n"
            "Incoming communications from the rival team's Coach (Long-Term Consciousness):\n"
            + "\n".join(comms_lines)
            + "\n\nYou can deliberate with the peer coach using tool `a2a_send_coach_message` "
            "to negotiate territory, hunting pacts, or non-aggression. Reflect any agreed strategy in your directives.\n\n"
        )
    else:
        peer_name = "coach(cat-2)" if target_cat == "cat-1" else "coach(cat-1)"
        comms_block = (
            "--- A2A STRATEGIC DIPLOMACY (PEER COACH COMMS) ---\n"
            f"(No incoming messages from peer coach {peer_name} yet. "
            f"You can use tool `a2a_send_coach_message` to initiate diplomacy, suggest territorial divisions, "
            "or propose hunting pacts.)\n\n"
        )

    return (
        f"{coach_identity_block}"
        f"{actor_manual_block}"
        f"{format_capabilities(records)}"
        f"{previous_block}"
        f"{notebook_block}"
        f"{transcript_block}"
        f"{comms_block}"
        f"{directive_block}"
        "Follow the three-step Evaluation Execution Protocol:\n"
        "1. Gather Telemetry & Strategic Comms: Query your tools (e.g. get_boxscore, get_coverage_map, pull_transcript, read_coach_notes, a2a_read_coach_messages, a2a_send_coach_message).\n"
        "2. Private Coach Note: Call write_coach_note to record your private deduction, hypothesis, or spatial pattern (with optional linked_ticks).\n"
        "3. Published Standing Orders: Return only the markdown bullet directives for the Cat Actor.\n"
        "Relax any 3–4 line condition: it is okay and expected for your dossier to grow "
        "as you make better observations, keeping in mind that the Cat Actor consumes these "
        "notes with high priority. Every directive must be legal and correct under your "
        "subordinate's manual above. Do not refer to tick numbers in the directives; the Cat cannot see them."
    )



def _is_final_event(event: Any) -> bool:
    """True when this event carries the model's answer rather than narration.

    ADK's `is_final_response()` is false for partial streaming frames and for
    any turn carrying a function call or response. Those are precisely the turns
    the evaluator used to publish: the coach calls `get_boxscore`, and the turn
    that issues that call can carry commentary alongside it. Treating such a turn
    as part of the answer is what put a box-score dump and a chain-of-thought
    aside into the Cat's standing orders.

    Wrapped rather than called inline for two reasons: it is the single decision
    that separates a clean dossier from a polluted one, so it deserves a name and
    a test; and it belongs to a library we do not own, so a change in its
    semantics should fail one obvious test instead of quietly corrupting the
    coaching channel.

    Fails *open* on error -- an unrecognised event is treated as narration, not as
    the answer. If this helper is wrong in that direction the run falls back to
    collecting every chunk, which is the old behaviour: ugly, but never silent.
    """
    try:
        return bool(event.is_final_response())
    except Exception:
        return False


# A markdown list item: `-`, `*`, `+`, or `1.` / `1)`, with at most three spaces
# of leading indent (more than that is a nested list or a code block).
_BULLET_RE = re.compile(r"^ {0,3}(?:[-*+]|\d+[.)])\s+\S")


def _strip_to_directives(text: str) -> str:
    """Drops narration that precedes the directive list.

    Second line of defence behind the final-response filter in `evaluate`. That
    filter removes narration that arrived in a *separate* turn, which is the
    failure we actually observed. This one removes narration that arrives in the
    *same* turn as the answer -- a model writing "Here is my analysis... therefore
    the revised directives are:" before the list it was asked for.

    The rule is "keep the trailing run of list items". Anchoring on the *first*
    bullet instead would not work, and the real failure shows why: that dossier
    rendered its box-score dump as a bullet list (``- `kills`: 16``), so the
    first bullet in the document was data, not a directive. The last contiguous
    block of bullets is far more reliably the answer, because the answer is the
    last thing a model writes.

    Two guards matter:

    * A line that is indented or blank does not break the run, so a directive
      wrapped across lines is not mistaken for prose and does not sever the
      list above it.
    * If the result would be empty -- no bullets at all, e.g. the model wrote a
      single prose paragraph -- the original text is returned untouched. Coaching
      that is badly formatted is still coaching; coaching that is gone is a
      silent regression to positional filler.

    This never reorders, rewrites or truncates the directives themselves.
    """
    if not text:
        return text

    lines = text.splitlines()
    # Index of the last line that is unambiguously prose: non-blank, flush left,
    # and not a list item. Everything after it is the directive block.
    last_prose = -1
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line[:1].isspace():
            continue  # continuation of the bullet above
        if not _BULLET_RE.match(line):
            last_prose = i

    if last_prose < 0:
        return text.strip()

    trimmed = "\n".join(lines[last_prose + 1:]).strip()
    if not trimmed:
        # Nothing followed the prose -- the model answered without a list. Keep
        # what it said rather than publishing an empty file.
        return text.strip()

    if last_prose >= 0:
        logger.warning(
            "Stripped %d line(s) of narration preceding the coach's directives.",
            last_prose + 1,
        )
    return trimmed


async def evaluate(
    records: List[Dict[str, Any]],
    agent_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Runs one evaluation pass over a transcript.

    Returns:
        (notes_markdown, source) where source is 'adk_gemini' or 'offline_fallback'.
    """
    if not records:
        return _summarise_without_model(records), "offline_fallback"

    # Skip live network calls during automated pytest runs unless explicitly enabled.
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        return _summarise_without_model(records), "offline_fallback"

    if not has_gemini_credentials():
        return _summarise_without_model(records), "offline_fallback"

    target_cat = agent_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    # Isolated session and user per cat agent to prevent state collision
    user_id = f"cat_evaluator_{target_cat}"
    session_id = f"session_cat_evaluator_{target_cat}"

    async with _eval_lock:
        # Stamped before the call so a run that times out is still attributed to
        # the budget that caused it. Reading the budget afterwards would credit
        # a timeout to whatever the dial had been moved to in the meantime.
        budget = get_eval_thinking_budget()
        timeout_sec = get_eval_timeout()
        started = time.monotonic()
        thoughts: Optional[int] = None
        timed_out = False
        try:
            # Each evaluation is independent; drop any prior session state.
            try:
                await eval_session_service.delete_session(
                    app_name="cat_evaluator_app", user_id=user_id, session_id=session_id
                )
            except Exception:
                pass
            await eval_session_service.create_session(
                app_name="cat_evaluator_app", user_id=user_id, session_id=session_id
            )

            standing_order = (get_coach_directive() or {}).get("directive")
            prompt = build_eval_prompt(
                records, store.read_coaching_notes(agent_id=target_cat), standing_order, agent_id=target_cat
            )

            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            # Two buckets, not one. `final_chunks` is the answer; `all_chunks` is
            # everything the model said, kept only as a fallback.
            #
            # The single-bucket version of this shipped a 70-line dossier into the
            # standing orders: a full box-score dump, an "Analysis of Existing
            # Directives" section, a chain-of-thought aside ("Wait, slash_attempts
            # is 15 ... This is a crucial finding!"), and then the directive list
            # twice over -- once with `*` bullets and once with `-`, welded
            # together mid-line because `"".join` adds no separator.
            #
            # None of that was the model disobeying. eval_skills.md 3 says "Write
            # only the bullets. Nothing else." and it complied -- in its *final*
            # turn. The rest was narration emitted alongside the `get_boxscore`
            # call, which splits one evaluation into several turns. The old code
            # collected text from every one of them.
            #
            # It stayed hidden while the evaluator's thinking budget was 0,
            # because an unthinking model answered in a single turn and there was
            # only ever one chunk to join. Raising the budget made the model plan,
            # call the tool, and narrate around it -- so the latent bug started
            # firing, intermittently, depending on whether a given run chose to
            # call the tool at all.
            final_chunks: List[str] = []
            all_chunks: List[str] = []
            tool_calls: List[Dict[str, Any]] = []

            async with asyncio.timeout(timeout_sec):
                agen = eval_runner.run_async(
                    user_id=user_id, session_id=session_id, new_message=msg
                )
                try:
                    while True:
                        event = await agen.__anext__()
                        if event.error_code or event.error_message:
                            raise RuntimeError(
                                f"ADK Error: {event.error_code} - {event.error_message}"
                            )
                        # Summed across events rather than taken from the last:
                        # a tool call splits one evaluation into several model
                        # turns, and reading only the final turn would report
                        # the thinking that followed the boxscore fetch while
                        # silently dropping the thinking that decided to fetch.
                        #
                        # Note this is the opposite policy to the text below, and
                        # deliberately so. Cost is incurred by every turn, so it
                        # accumulates; the answer is produced by one turn, so it
                        # does not. Conflating the two is what caused the bug.
                        usage = getattr(event, "usage_metadata", None)
                        spent = getattr(usage, "thoughts_token_count", None)
                        if spent is not None:
                            thoughts = (thoughts or 0) + spent
                        if not (event.content and event.content.parts):
                            continue

                        # Capture tool calls: function_call on call, function_response on result
                        for p in event.content.parts:
                            fc = getattr(p, "function_call", None)
                            if fc and getattr(fc, "name", None):
                                tool_calls.append({"name": fc.name, "ok": True})
                            fr = getattr(p, "function_response", None)
                            if fr and getattr(fr, "name", None) and tool_calls:
                                # If function_response indicates failure or error, mark ok: False
                                resp = getattr(fr, "response", {})
                                if isinstance(resp, dict):
                                    inner = resp.get("response") if "response" in resp else resp
                                    if isinstance(inner, dict) and inner.get("available") is False:
                                        for tc in reversed(tool_calls):
                                            if tc["name"] == fr.name:
                                                tc["ok"] = False
                                                break

                        # See `_is_final_event`: this is what separates the
                        # answer from narration emitted around a tool call.
                        is_final = _is_final_event(event)
                        for p in event.content.parts:
                            if not p.text or getattr(p, "thought", False):
                                continue
                            all_chunks.append(p.text)
                            if is_final:
                                final_chunks.append(p.text)
                except StopAsyncIteration:
                    pass

            # Prefer the final response. Fall back to everything only if the run
            # produced no final-response text at all -- publishing a polluted
            # dossier is bad, publishing nothing is worse, because a blank reply
            # drops through to `_summarise_without_model` and the positional
            # filler overwrites the real standing orders.
            notes = "".join(final_chunks).strip()
            if not notes:
                notes = "".join(all_chunks).strip()
                if notes:
                    logger.warning(
                        "Evaluator produced no final-response text; falling back "
                        "to the full transcript of %d part(s).",
                        len(all_chunks),
                    )

            notes = _strip_to_directives(notes)
            if notes:
                return notes, "adk_gemini"
            logger.warning("Evaluator returned empty notes; using offline fallback.")

        except asyncio.TimeoutError:
            timed_out = True
            logger.warning(
                "Evaluation abandoned after %gs (thinking budget %s). The offline "
                "fallback will overwrite the published standing orders.",
                timeout_sec,
                describe_thinking_budget(budget),
            )
        except Exception as exc:
            err = str(exc)
            mark_auth_error(err)
            logger.warning("Evaluator execution failure: %s", err)
        finally:
            _last_eval_telemetry.update({
                "thinking_budget": budget,
                "thoughts_token_count": thoughts,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "timed_out": timed_out,
                "tool_calls": tool_calls,
            })

    return _summarise_without_model(records), "offline_fallback"


async def evaluate_and_publish(agent_id: Optional[str] = None) -> Dict[str, Any]:
    """Reads the current transcript, evaluates it, and publishes coaching notes.

    This is the entry point the API server dispatches to every N ticks. It owns the
    full read-evaluate-write cycle so the caller only has to fire and forget.
    """
    target_id = agent_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    records = store.read_transcript_records(agent_id=target_id)
    notes, source = await evaluate(records, agent_id=target_id)

    tick_range = None
    if records:
        tick_range = f"{records[0].get('tick')}-{records[-1].get('tick')}"
    store.write_coaching_notes(notes, tick_range=tick_range, agent_id=target_id)

    result = {
        "agent_id": target_id,
        "ticks_evaluated": len(records),
        "tick_range": tick_range,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    logger.info(
        "Evaluation published for %s: %s ticks (%s) via %s",
        target_id,
        len(records),
        tick_range,
        source,
    )
    return result


# ---------------------------------------------------------------------------
# General Radio
#
# The Commander's direct, conversational channel to the coach. Entirely off the
# evaluation cycle: calling it never publishes notes, never advances the tick
# counter, and never touches coaching_notes.md. Asking the coach a question and
# changing the coach's orders are two separate acts, and the caller has to opt
# into the second one explicitly.
# ---------------------------------------------------------------------------


def _general_offline_reply(user_prompt: str, notes: str, agent_id: str = "cat-1") -> str:
    """What the Coach says on the General's radio when no model is reachable.

    Deliberately says nothing about performance. The offline path on the
    evaluation side can get away with positional filler because something must
    be published; here the honest answer is that the officer is off the air.
    Inventing a plausible-sounding coaching rationale would be worse than
    silence -- the General is on this channel precisely to audit the coaching,
    and a fabricated defence of it is the one reply that actively misleads them.
    """
    count = sum(1 for ln in (notes or "").splitlines() if ln.strip().startswith("-"))
    return (
        f"General, the Coach for {agent_id.upper()} is off the air -- no model backend is reachable "
        "from the evaluator, so I cannot reason about your question or pull the "
        f"box score. My dossier currently stands at {count} directive(s) and is "
        f"still being delivered to {agent_id.upper()} unchanged. Nothing about the coaching "
        "has been altered by this transmission."
    )


async def interrogate_general(
    user_prompt: str,
    notes: Optional[str] = None,
    records: Optional[List[Dict[str, Any]]] = None,
    standing_order: Optional[str] = None,
    tick: Optional[int] = None,
    agent_id: Optional[str] = None,
) -> Tuple[str, str, Optional[str]]:
    """Puts a question to the coach and returns its prose reply.

    Args:
        user_prompt: what the Commander said.
        notes: the dossier currently in force. Read from the store when omitted.
        records: recent transcript rows, for questions about what the coach saw.
        standing_order: the Commander's existing standing order, so the coach
            answers in light of it instead of contradicting an order it is
            already under.
        tick: the game tick, for the log line only. Never shown to the model --
            the coach is held to the same no-tick-citations rule everywhere else.
        agent_id: target cat ID to scope coaching dossier and session.

    Returns:
        (reply_text, engine, error) where engine is 'adk_gemini' or
        'offline_fallback'.
    """
    target_cat = agent_id or os.environ.get("CAT_ID") or ("cat-2" if os.environ.get("PORT") == "8003" else "cat-1")
    clean = (user_prompt or "").strip()
    if notes is None:
        notes = store.read_coaching_notes(agent_id=target_cat)

    if not clean:
        return (
            "Commander, I received an empty transmission.",
            "offline_fallback",
            None,
        )

    # Same guard the evaluation path uses: no live network calls under pytest
    # unless explicitly enabled.
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        return _general_offline_reply(clean, notes, agent_id=target_cat), "offline_fallback", None

    if not has_gemini_credentials():
        return _general_offline_reply(clean, notes, agent_id=target_cat), "offline_fallback", None

    user_id = f"cat_coach_{target_cat}"
    session_id = f"session_cat_coach_{target_cat}"

    async with _general_lock:
        try:
            # Torn down and recreated per call, exactly as the actor's
            # interrogator does it. The persona is re-read from disk on every
            # run, and a surviving session would keep replaying the old one
            # back to the model as conversation history -- so an edit to
            # general_radio_skills.md would appear to have no effect.
            try:
                await eval_session_service.delete_session(
                    app_name="cat_evaluator_app",
                    user_id=user_id,
                    session_id=session_id,
                )
            except Exception:
                pass
            await eval_session_service.create_session(
                app_name="cat_evaluator_app",
                user_id=user_id,
                session_id=session_id,
            )

            coach_identity_block = (
                f"--- YOUR ASSIGNMENT: COACH FOR {target_cat.upper()} ---\n"
                f"You are the dedicated tactical coach exclusively assigned to {target_cat.upper()}.\n\n"
            )

            dossier_block = (
                "--- YOUR DOSSIER, CURRENTLY IN FORCE ---\n"
                f"{notes.strip()}\n\n"
                f"These are your own directives. They are being delivered to "
                f"{target_cat.upper()} verbatim, every tick, right now.\n\n"
                if (notes or "").strip()
                else
                "--- YOUR DOSSIER, CURRENTLY IN FORCE ---\n"
                "You have not published any directives yet.\n\n"
            )

            order_block = ""
            if standing_order and standing_order.strip():
                order_block = (
                    f"{COACH_DIRECTIVE_BLOCK_HEADER}\n"
                    f"{standing_order.strip()}\n\n"
                    "You are already under this order from the Commander. Answer "
                    "in light of it.\n\n"
                )

            transcript_block = ""
            if records:
                transcript_block = (
                    f"--- RECENT CAT TRANSCRIPT ({len(records)} ticks, oldest "
                    f"first) ---\n{format_transcript(records)}\n\n"
                )

            prompt = (
                f"{coach_identity_block}"
                f"{dossier_block}"
                f"{order_block}"
                f"{transcript_block}"
                "--- GENERAL RADIO ---\n"
                f'COMMANDER: "{clean}"\n'
            )

            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            reply_parts: List[str] = []

            async with asyncio.timeout(GENERAL_RADIO_TIMEOUT_SECONDS):
                agen = general_runner.run_async(
                    user_id=user_id, session_id=session_id, new_message=msg
                )
                try:
                    while True:
                        event = await agen.__anext__()
                        if event.error_code or event.error_message:
                            raise RuntimeError(
                                f"ADK Error: {event.error_code} - {event.error_message}"
                            )
                        if event.content and event.content.parts:
                            for p in event.content.parts:
                                if p.text and not getattr(p, "thought", False):
                                    reply_parts.append(p.text)
                except StopAsyncIteration:
                    pass

            reply_text = "".join(reply_parts).strip()
            if reply_text:
                logger.info("General radio replied (tick %s)", tick)
                return reply_text, "adk_gemini", None
            return (
                "Commander, I received your transmission but have nothing to add "
                "beyond the dossier as it stands.",
                "adk_gemini",
                None,
            )

        except asyncio.TimeoutError:
            err = f"general radio timed out after {GENERAL_RADIO_TIMEOUT_SECONDS:.0f}s"
            logger.warning("%s", err)
            return (
                f"Commander, I ran out of time composing a reply "
                f"({GENERAL_RADIO_TIMEOUT_SECONDS:.0f}s). My dossier is unchanged "
                "and still reaching the Cat. Try a narrower question.",
                "offline_fallback",
                err,
            )
        except Exception as exc:
            err = str(exc)
            logger.warning("General radio error: %s", err)
            return (
                f"General, the link to the Coach broke: {err}. My dossier is "
                "unchanged and still reaching the Cat.",
                "offline_fallback",
                err,
            )
