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
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
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

    base = (os.environ.get(GAME_SERVER_URL_ENV) or DEFAULT_GAME_SERVER_URL).rstrip("/")
    url = f"{base}/api/simulations/{BOXSCORE_SIM_ID}/boxscore"

    try:
        async with httpx.AsyncClient(timeout=BOXSCORE_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return _boxscore_unavailable(
                    f"game server returned HTTP {resp.status_code}"
                )
            payload = resp.json()
    except Exception as exc:
        # Includes connect errors and timeouts. The coach still has the
        # transcript, so a missing box score degrades the notes rather than
        # failing the evaluation.
        logger.warning("Box score fetch failed: %s: %s", type(exc).__name__, exc)
        return _boxscore_unavailable(f"{type(exc).__name__}: {exc}")

    if not isinstance(payload, dict):
        return _boxscore_unavailable("game server returned an unexpected payload")

    cats = payload.get("cats")
    if not isinstance(cats, list) or not cats:
        return _boxscore_unavailable("game server reported no cats")

    tick = payload.get("tick")
    since = payload.get("tracking_since_tick")
    # Emitted because `tick` alone does not say how much of the game was
    # measured: a tracker attached mid-game reports partial counters.
    measured = tick - since if isinstance(tick, int) and isinstance(since, int) else None

    return {
        "available": True,
        "simulation_id": payload.get("simulation_id"),
        "status": payload.get("status"),
        "tick": tick,
        "round": payload.get("round"),
        "tracking_since_tick": since,
        "ticks_measured": measured,
        "arena": payload.get("arena"),
        "leader": payload.get("leader"),
        # Every cat, verbatim. The comparison against the rival is the whole
        # point: filtering to cat-1 here would leave the coach with a number and
        # nothing to measure it against.
        "cats": cats,
    }


_initial_eval_budget: int = _initial_from_env(
    EVAL_THINKING_BUDGET_ENV,
    coerce_eval_thinking_budget,
    THINKING_BUDGET_OFF,
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
    tools=[get_boxscore],
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
            "Your vision cone is forward-only, so those bearings are unobserved."
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
        + retuned
        + "\nThese are the live values from the game server and they override "
        "every illustrative figure printed in the manual above.\n\n"
    )


def build_eval_prompt(records: List[Dict[str, Any]], previous: str) -> str:
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

    # No tick indices reach the model -- not on the lines, not in this
    # header. See format_transcript() for why.
    return (
        f"{actor_manual_block}"
        f"{format_capabilities(records)}"
        f"{previous_block}"
        f"--- CAT TRANSCRIPT ({len(records)} consecutive ticks, "
        f"oldest first) ---\n"
        f"{format_transcript(records)}\n\n"
        "Write your standing orders for the Cat now, as markdown bullets. "
        "Every directive must be legal and correct under your subordinate's "
        "manual above. Do not refer to tick numbers; the Cat cannot see them."
    )


async def evaluate(records: List[Dict[str, Any]]) -> Tuple[str, str]:
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

    # NOTE: no tick bounds are computed here on purpose. They previously fed the
    # prompt header and were the last route by which an absolute tick index
    # reached the model. The published `tick_range` is derived separately in
    # evaluate_and_publish(), which is metadata for the UI, not model input.
    user_id = "cat_evaluator"
    session_id = "session_cat_evaluator"

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

            prompt = build_eval_prompt(records, store.read_coaching_notes())

            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            chunks: List[str] = []

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
                        usage = getattr(event, "usage_metadata", None)
                        spent = getattr(usage, "thoughts_token_count", None)
                        if spent is not None:
                            thoughts = (thoughts or 0) + spent
                        if event.content and event.content.parts:
                            for p in event.content.parts:
                                if p.text and not getattr(p, "thought", False):
                                    chunks.append(p.text)
                except StopAsyncIteration:
                    pass

            notes = "".join(chunks).strip()
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
            })

    return _summarise_without_model(records), "offline_fallback"


async def evaluate_and_publish() -> Dict[str, Any]:
    """Reads the current transcript, evaluates it, and publishes coaching notes.

    This is the entry point the API server dispatches to every N ticks. It owns the
    full read-evaluate-write cycle so the caller only has to fire and forget.
    """
    records = store.read_transcript_records()
    notes, source = await evaluate(records)

    tick_range = None
    if records:
        tick_range = f"{records[0].get('tick')}-{records[-1].get('tick')}"
    store.write_coaching_notes(notes, tick_range=tick_range)

    result = {
        "ticks_evaluated": len(records),
        "tick_range": tick_range,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    logger.info(
        "Evaluation published: %s ticks (%s) via %s",
        len(records),
        tick_range,
        source,
    )
    return result
