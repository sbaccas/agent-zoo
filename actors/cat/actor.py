import asyncio
import math
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# Ensure environment is loaded
load_dotenv()
if "GOOGLE_CLOUD_REGION" in os.environ and "GOOGLE_CLOUD_LOCATION" not in os.environ:
    os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ["GOOGLE_CLOUD_REGION"]

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from actors.models import CatTacticalDecision, Observation
from actors.cat import store

# Skills manual path and loader
SKILLS_PATH = Path(__file__).parent / "actor_skills.md"
RADIO_SKILLS_PATH = Path(__file__).parent / "radio_skills.md"

# Section delimiters for the decision prompt.
#
# These are labels, not instructions. Everything about how much weight a block
# carries lives in actor_skills.md, which refers to these blocks by exactly
# these strings -- see tests/test_prompt_is_data_only.py, which fails if a
# header is renamed here without being renamed there.
COACH_BLOCK_HEADER = "--- EVALUATOR NOTES ---"
COMMANDER_BLOCK_HEADER = "--- COMMANDER RADIO ---"

# Age in ticks at which a commander order stops being emitted last. This is a
# layout policy, not a verdict: the order is still delivered in full, still
# carries its tick and its age in ticks, and is still the model's to judge.
# Only its position changes, from the slot nearest the decision to the one
# furthest from it.
#
# Sized from the live distribution rather than picked: across 198 prompts that
# carried an order, the median age was 42 ticks and 43% were older than 50. A
# threshold of 20 therefore demotes most of what was accumulating in the most
# influential slot in the prompt, while leaving genuinely live orders -- the
# ones a human just typed -- exactly where they were.
COMMANDER_FRESH_TICKS = 20


# --- Thinking budget ------------------------------------------------------
#
# How many tokens of private scratchpad the actor gets before it must emit its
# answer. Two values are special and are not token counts at all:
#
#   0   DISABLED -- no scratchpad. The model's first emitted token is already
#       part of the answer.
#   -1  AUTOMATIC -- the model decides per request, up to ~8,192 tokens.
#
# The model the actor runs on. Named rather than repeated as a literal at each
# LlmAgent below, because the budget bounds are derived from it and the two
# drifting apart would mean offering a range the model rejects.
ACTOR_MODEL = "gemini-2.5-flash"

THINKING_BUDGET_OFF = 0
THINKING_BUDGET_AUTO = -1

# Per-model finite ranges, from the Gemini thinking documentation. The floors
# differ and matter: 2.5 Pro cannot be switched off at all and rejects anything
# under 128, so a UI that offers 0-24,576 for every model is wrong for two of
# the three listed here.
#
# A model missing from this table falls back to the widest range any of them
# allows, and the model itself remains the authority -- an out-of-range budget
# comes back as an API error on the next tick, surfaced as the fallback reason.
MODEL_THINKING_BOUNDS = {
    "gemini-2.5-flash": (1, 24576),
    "gemini-2.5-flash-lite": (512, 24576),
    "gemini-2.5-pro": (128, 32768),
}
_DEFAULT_BOUNDS = (1, 32768)

def thinking_bounds_for(model: str) -> Tuple[int, int, bool]:
    """``(min, max, can_disable)`` for a model.

    Parameterised by model rather than hardwired to the actor because the
    evaluator subagent has the same dial and may not be on the same model. Two
    copies of this table would eventually disagree, and the way that failure
    presents is a slider offering a range the model rejects on every call --
    which reads as the agent being broken rather than as the UI being wrong.
    """
    low, high = MODEL_THINKING_BOUNDS.get(model, _DEFAULT_BOUNDS)
    return low, high, not model.startswith("gemini-2.5-pro")


THINKING_BUDGET_MIN, THINKING_BUDGET_MAX, THINKING_CAN_BE_DISABLED = (
    thinking_bounds_for(ACTOR_MODEL)
)


# The budget this process starts on. Seeded from the environment so a run can be
# launched into a configuration without anyone having to remember to move a
# slider first -- which matters because the first ticks of a game are recorded
# into the same transcript as the rest of it.
#
# Default 512. This was 0 for the whole history of the repo, then 256, and each
# move is deliberate rather than incidental.
#
# The evidence for leaving 0: at a budget of 128 the measured
# `thoughts_token_count` sat at p50=125 / p90=129 across 781 consecutive calls,
# i.e. the model was saturating the allowance on essentially every tick. A
# ceiling that is hit every single time is not a budget, it is a truncation, and
# the work being truncated is a per-mouse frame rotation the observation does
# not do for the model.
#
# The evidence for going past 256 is that the vision box got bigger. The default
# `vision_half_width` is now 5, making the viewport 11 tiles wide rather than 7,
# so on a busy tick there are simply more mice to reason about -- and the per-
# mouse cost is what this budget pays for.
#
# Note the interaction with **aim assist**. When it is on, the engine ships
# `forward`, `offaxis` and `in_strike_zone` per entity and the frame rotation
# disappears entirely, so the scratchpad stops being arithmetic scribble and
# becomes target selection and route planning. The budget is not wasted in that
# mode, but what it buys is different, and a future measurement should separate
# the two rather than averaging across them.
#
# Two things this does NOT do, recorded here so nobody assumes otherwise:
#   - It does not make the actor slow. 512 tokens is ~2.6s of generation against
#     a deliberation ceiling that affords ~1,340 tokens, so the timeout is still
#     not binding at this setting -- though the margin is now roughly 2.6x
#     rather than 5x, and raising this further should come with a look at
#     `suggested_deliberation_timeout`.
#   - It does not re-validate the prompt schema. CatTacticalDecision puts
#     `thought` before `action` *because* there was no scratchpad, and that
#     10/10 vs 1/10 result was measured at budget 0. It has not been re-measured
#     with a real scratchpad available, and the field order is retained on the
#     assumption that it is still neutral-to-helpful rather than on evidence.
THINKING_BUDGET_DEFAULT = 512

DEFAULT_THINKING_BUDGET_ENV = "CAT_THINKING_BUDGET"

# How long a single Gemini decision call may run before it is abandoned and the
# heuristic covers the tick.
#
# Adjustable at runtime, for the same reason the budget is: the two are one
# setting wearing two hats. Thinking tokens are emitted before the first answer
# token, so at ~200 tok/s an 8s ceiling affords roughly 1,300 tokens of thought
# and no more. Leaving this fixed while exposing the budget would have produced
# a dial that appears to go to 24,576 but silently stops working above ~1,300 --
# every larger setting timing out, falling back to the reflex, and reporting
# itself in the box score as a run where thinking did not help.
#
# Raising it is cheap here specifically because deliberation is decoupled from
# the tick loop: a slow call does not stall the game, it just means the reflex
# covers more ticks while the model is busy. What it does cost is staleness --
# a decision built at tick N and applied at N+30 is answering a question about
# a board that has moved -- which the prompt inspector already surfaces per
# decision as the gap between `built_at_tick` and `applied_at_tick`.
DELIBERATION_TIMEOUT_DEFAULT_SEC = 8.0
DELIBERATION_TIMEOUT_MIN_SEC = 1.0
# A ceiling on the ceiling. Past this, a decision is near-certainly answering a
# board that no longer exists, and the run stops being a test of thinking and
# becomes a test of how well the cat plays on minute-old information.
DELIBERATION_TIMEOUT_MAX_SEC = 120.0


def _initial_deliberation_timeout() -> float:
    raw = os.environ.get("CAT_DELIBERATION_TIMEOUT_SEC")
    if raw is None or not str(raw).strip():
        return DELIBERATION_TIMEOUT_DEFAULT_SEC
    try:
        return coerce_deliberation_timeout(raw)
    except ValueError as exc:
        print(
            f"⚠️  [ACTOR] Ignoring CAT_DELIBERATION_TIMEOUT_SEC={raw!r}: {exc}. "
            f"Using {DELIBERATION_TIMEOUT_DEFAULT_SEC:g}s.",
            flush=True,
        )
        return DELIBERATION_TIMEOUT_DEFAULT_SEC


def coerce_deliberation_timeout(value: Any) -> float:
    """Validates a deliberation timeout in seconds, or raises ValueError."""
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"deliberation timeout must be a number, got {value!r}")
    if not (DELIBERATION_TIMEOUT_MIN_SEC <= timeout <= DELIBERATION_TIMEOUT_MAX_SEC):
        raise ValueError(
            f"deliberation timeout {timeout:g}s is out of range: use "
            f"{DELIBERATION_TIMEOUT_MIN_SEC:g}-{DELIBERATION_TIMEOUT_MAX_SEC:g} seconds"
        )
    return timeout


# Mutable, and read through `get_deliberation_timeout()` at the point of use
# rather than captured at import. A module-level name imported elsewhere with
# `from ... import DELIBERATION_TIMEOUT_SEC` would bind a copy that never
# changes -- the exact class of bug the budget's single-source-of-truth
# treatment is designed to avoid.
_deliberation_timeout_sec: float = _initial_deliberation_timeout()


def get_deliberation_timeout() -> float:
    """Seconds a decision call may run before it is abandoned."""
    return _deliberation_timeout_sec


def set_deliberation_timeout(value: Any) -> float:
    """Changes the abandon ceiling. Applies to the next call, not in-flight ones."""
    global _deliberation_timeout_sec
    _deliberation_timeout_sec = coerce_deliberation_timeout(value)
    return _deliberation_timeout_sec



def coerce_thinking_budget(value: Any, model: Optional[str] = None) -> int:
    """Turns arbitrary input into a budget the model will actually accept.

    Raises ValueError rather than clamping silently. A clamp would let a request
    for 60,000 tokens be recorded in the archive as the 24,576 it was quietly
    reduced to, and the whole point of the archive is that the configuration it
    reports is the one that ran.

    The single exception is the AUTO sentinel, which is a mode rather than a
    quantity and so is passed through untouched.

    ``model`` defaults to the actor's. It is a parameter rather than a constant
    so the evaluator subagent validates against *its* model: the two are the
    same today, and a shared validator that silently assumed so would approve a
    budget the other model rejects the first time they diverge.
    """
    model = model or ACTOR_MODEL
    low, high, can_disable = thinking_bounds_for(model)

    try:
        budget = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"thinking budget must be an integer, got {value!r}")

    if budget == THINKING_BUDGET_AUTO:
        return budget
    if budget == THINKING_BUDGET_OFF:
        if can_disable:
            return budget
        raise ValueError(
            f"thinking cannot be disabled on {model}; the lowest it will "
            f"accept is {low} tokens"
        )
    if low <= budget <= high:
        return budget
    raise ValueError(
        f"thinking budget {budget} is out of range for {model}: use "
        f"{THINKING_BUDGET_OFF} (off), {THINKING_BUDGET_AUTO} (auto), or "
        f"{low}-{high} tokens"
    )


def describe_thinking_budget(budget: Optional[int]) -> str:
    """A label for a budget, for logs, the archive and the UI.

    One function so the three cannot disagree about what `-1` means.
    """
    if budget is None:
        return "unset"
    if budget == THINKING_BUDGET_OFF:
        return "off (no scratchpad)"
    if budget == THINKING_BUDGET_AUTO:
        return "auto (model decides)"
    return f"{budget:,} tokens"


def _initial_thinking_budget() -> int:
    """Reads the startup budget from the environment, falling back to 0.

    A malformed value is a warning and a fall back to THINKING_BUDGET_DEFAULT,
    not a crash: the workbench failing to boot over a typo in a tuning variable
    would be a worse outcome than starting on the documented default.
    The value is echoed either way, because a silently ignored setting is how
    you end up comparing two runs that were actually identical.
    """
    raw = os.environ.get(DEFAULT_THINKING_BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return THINKING_BUDGET_DEFAULT
    try:
        budget = coerce_thinking_budget(raw)
    except ValueError as exc:
        print(
            f"⚠️  [ACTOR] Ignoring {DEFAULT_THINKING_BUDGET_ENV}={raw!r}: {exc}. "
            f"Starting on the default: "
            f"{describe_thinking_budget(THINKING_BUDGET_DEFAULT)}.",
            flush=True,
        )
        return THINKING_BUDGET_DEFAULT
    print(
        f"🧠 [ACTOR] Thinking budget from {DEFAULT_THINKING_BUDGET_ENV}: "
        f"{describe_thinking_budget(budget)}",
        flush=True,
    )
    return budget


def get_skills_instruction() -> str:
    """The actor's entire behavioural specification, read fresh from disk.

    There is deliberately no inline fallback. A previous fallback string told
    the cat to "close distance with lunges", contradicting the manual, and
    would have applied silently had the file ever gone missing.
    """
    if not SKILLS_PATH.exists():
        raise FileNotFoundError(
            f"Actor skills manual missing at {SKILLS_PATH}. The agent has no "
            f"behavioural specification without it; refusing to run on an "
            f"improvised one."
        )
    return SKILLS_PATH.read_text(encoding="utf-8")


def get_radio_instruction() -> str:
    """Persona for free-form Commander radio replies, read fresh from disk."""
    if not RADIO_SKILLS_PATH.exists():
        raise FileNotFoundError(
            f"Radio persona missing at {RADIO_SKILLS_PATH}."
        )
    return RADIO_SKILLS_PATH.read_text(encoding="utf-8")


def _actor_instruction_provider(_ctx: ReadonlyContext) -> str:
    """Re-reads actor_skills.md on every decision so edits apply immediately.

    Previously the manual was read once at import. uvicorn's --reload only
    watches .py files, so editing the markdown changed nothing until an unrelated
    Python file was touched -- silently, with no error and no warning.
    """
    return get_skills_instruction()


def _interrogator_instruction_provider(_ctx: ReadonlyContext) -> str:
    """The same manual, plus the radio persona. Both read fresh from disk."""
    return get_skills_instruction() + "\n\n" + get_radio_instruction()


# Initialize ADK Agent and Runner
#
# The thinking budget here is a *starting* value, not a fixed one. It is
# rebound at runtime by `set_thinking_budget` and must therefore never be read
# from this literal anywhere else -- `get_thinking_budget` reads the live agent.
adk_agent = LlmAgent(
    name="cat_tactician",
    model=ACTOR_MODEL,
    instruction=_actor_instruction_provider,
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
    app_name="cat_agent_app",
    session_service=session_service,
)

# Initialize Dedicated Interrogation / Commander Radio Agent & Runner (Free-form conversational reasoning)
#
# Deliberately NOT wired to `set_thinking_budget`. This agent answers the
# human on the radio; it is off the tick loop, so its latency costs nobody a
# turn, and including it would mean an A/B on the actor's budget silently
# changed the radio's behaviour too. Whatever the experiment concludes, it
# should be a conclusion about the thing that plays the game.
adk_interrogator_agent = LlmAgent(
    name="cat_interrogator",
    model=ACTOR_MODEL,
    instruction=_interrogator_instruction_provider,
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    ),
)
adk_interrogator_runner = Runner(
    agent=adk_interrogator_agent,
    app_name="cat_agent_app",
    session_service=session_service,
)


def summarize_agent(agent: Any) -> Dict[str, Any]:
    """The settings an agent is actually running with, for the game archive.

    Introspected off the live object rather than restated as a literal, so it
    cannot drift from the code it describes. A hand-written dict would keep
    reporting `thinking_budget: 0` for months after someone raised it, and the
    archive's whole purpose is to tell two runs apart.

    Every lookup is defensive because this is diagnostics: a future ADK version
    that renames a field should cost a null in a manifest, not a failed
    startup.
    """
    cfg = getattr(agent, "generate_content_config", None)
    thinking = getattr(cfg, "thinking_config", None)
    schema = getattr(agent, "output_schema", None)
    budget = getattr(thinking, "thinking_budget", None)
    return {
        "name": getattr(agent, "name", None),
        "model": getattr(agent, "model", None),
        "thinking_budget": budget,
        # Spelled out alongside the number because `-1` reads as a bug to
        # anyone opening an archive months later, not as "the model chose".
        "thinking_budget_label": describe_thinking_budget(budget),
        "include_thoughts": getattr(thinking, "include_thoughts", None),
        "temperature": getattr(cfg, "temperature", None),
        "max_output_tokens": getattr(cfg, "max_output_tokens", None),
        "output_schema": getattr(schema, "__name__", None),
        "tools": sorted(
            getattr(t, "__name__", str(t)) for t in (getattr(agent, "tools", None) or [])
        ),
    }


def get_thinking_budget() -> Optional[int]:
    """The budget the actor is running with right now, read off the live agent.

    Never mirrored into a separate variable. The one thing this feature must not
    do is let the UI, the archive and the model disagree about what is in force,
    and the only way to guarantee that is to have a single place that knows.
    """
    cfg = getattr(adk_agent, "generate_content_config", None)
    thinking = getattr(cfg, "thinking_config", None)
    return getattr(thinking, "thinking_budget", None)


def set_thinking_budget(value: Any) -> int:
    """Rebinds the actor's thinking budget. Takes effect on the next decision.

    ADK reads `agent.generate_content_config` afresh when it builds each
    request (see `google.adk.flows.llm_flows.basic._build_basic_request`), so
    nothing needs restarting for this to apply.

    A *new* ThinkingConfig is assigned rather than the existing one being
    mutated in place. That copy in ADK is shallow, so an in-flight request holds
    a reference to the very object a mutation would edit -- a call that started
    under the old budget would finish under the new one, and the prompt log
    would attribute it to whichever happened to be set when it landed. Swapping
    the object leaves any in-flight request on the config it started with.

    Returns the budget now in force. Raises ValueError if the model would not
    accept it; the caller is expected to surface that rather than swallow it.
    """
    budget = coerce_thinking_budget(value)
    cfg = adk_agent.generate_content_config
    if cfg is None:
        adk_agent.generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=budget)
        )
    else:
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=budget)
    return budget


# Rough model throughput, used only to relate a token budget to a wall-clock
# ceiling. Deliberately a constant rather than measured: it drives an advisory
# message, and a self-tuning estimate that quietly shifted under a long run
# would make two identical settings warn differently.
_BASELINE_LATENCY_SEC = 1.3
_OUTPUT_TOKENS_PER_SEC = 200.0

# What AUTO can reach on 2.5 Flash, per Google's documentation.
_AUTO_TYPICAL_CEILING = 8192


def estimate_affordable_tokens(timeout_sec: float, baseline_latency_sec: float) -> int:
    """Thinking tokens that fit in `timeout_sec`, given fixed per-call overhead.

    `baseline_latency_sec` is everything the call spends that is *not* thinking:
    connection, prompt ingestion, and the answer itself. It is a parameter
    because the two agents are nowhere near each other on it -- the actor sends
    a few kilobytes and answers in one short JSON object, while the evaluator
    ships an entire game transcript and makes a tool call first. Sharing the
    actor's 1.3s figure with the evaluator would have told you 24,576 tokens fit
    comfortably in 60s when in practice the fixed cost eats a third of it.
    """
    return int(max(0.0, timeout_sec - baseline_latency_sec) * _OUTPUT_TOKENS_PER_SEC)


def estimate_required_timeout(
    tokens: int, baseline_latency_sec: float, max_timeout_sec: float
) -> float:
    """Seconds a budget of `tokens` needs, capped at the setter's own maximum.

    Rounded up to the next whole second; a suggestion precise to three decimal
    places would imply a confidence this estimate does not have.
    """
    needed = baseline_latency_sec + (tokens / _OUTPUT_TOKENS_PER_SEC)
    return float(min(max_timeout_sec, math.ceil(needed)))


def nominal_thinking_tokens(budget: Optional[int]) -> Optional[int]:
    """How many tokens a budget is likely to actually spend, or None if no cap.

    AUTO is a mode, not a number, so it is resolved to the documented ceiling
    the model will reach on its own. OFF resolves to None rather than 0 because
    callers use None to mean "nothing to warn about".
    """
    if budget is None or budget == THINKING_BUDGET_OFF:
        return None
    return _AUTO_TYPICAL_CEILING if budget == THINKING_BUDGET_AUTO else budget


def affordable_thinking_tokens(timeout_sec: Optional[float] = None) -> int:
    """Roughly how many thinking tokens fit inside the deliberation ceiling.

    An estimate, and stated as one wherever it is shown. Its job is to stop
    someone burning an afternoon on a run that was timing out on every tick, not
    to predict latency.
    """
    if timeout_sec is None:
        timeout_sec = get_deliberation_timeout()
    return estimate_affordable_tokens(timeout_sec, _BASELINE_LATENCY_SEC)


def suggested_deliberation_timeout(budget: Optional[int]) -> Optional[float]:
    """The timeout a given budget would need, or None if the current one fits.

    Offered so the warning can carry a fix rather than just an objection.
    Capped at the same maximum the setter enforces.
    """
    tokens = nominal_thinking_tokens(budget)
    if tokens is None or tokens <= affordable_thinking_tokens():
        return None
    return estimate_required_timeout(
        tokens, _BASELINE_LATENCY_SEC, DELIBERATION_TIMEOUT_MAX_SEC
    )


def thinking_budget_warning(budget: Optional[int]) -> Optional[str]:
    """Flags budgets the tick loop cannot actually afford, or None if fine.

    Advisory, not enforcement: the point of the experiment is to find out where
    the cliff is, so the UI must let you walk off it -- while saying so.

    The numbers below are latency, not correctness. A decision that arrives
    after the deliberation timeout is discarded and the tick is played by the
    System-1 reflex instead, so an over-ambitious budget does not produce a
    slow, thoughtful cat: it produces a cat that is not using the model at all,
    while the box score still says Gemini was configured.
    """
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
        f"Decisions that overrun are discarded and the tick is played by the "
        f"reflex heuristic instead, so the run would measure the heuristic, not "
        f"the model. Raise the timeout to about {suggested:g}s to give this "
        f"budget room to land."
    )


_llm_lock = asyncio.Lock()

_auth_cache: Dict[str, Any] = {"timestamp": 0.0, "result": None}


def is_auth_error(error_msg: str) -> bool:
    """True only for failures that genuinely indicate bad or expired credentials.

    Deliberately narrow. Everything the model layer can throw -- timeouts, 429
    quota, 503, malformed responses -- used to land here too, which flipped the
    agent into a "credentials expired" state it would then report to the user for
    the next five seconds. A transient blip must not look like a dead token.

    Note the absence of a bare "token" test: it previously matched messages such
    as "max output tokens exceeded", classifying an ordinary generation-limit
    error as an expired credential.
    """
    if not error_msg:
        return False
    low = error_msg.lower()
    return (
        "refresherror" in low
        # Stem match: gcloud emits "Reauthentication is needed", not "reauthenticate".
        or "reauthenticat" in low
        or "invalid_grant" in low
        or "unauthorized" in low
        or "unauthenticated" in low
        or "permission_denied" in low
        or "permission denied" in low
        or "401" in error_msg
        or "403" in error_msg
        or ("expired" in low and "token" in low)        # "token has expired"
        or "default credentials" in low
        or "could not automatically determine credentials" in low
        # An unusable API key is a credential problem, not a transient blip.
        or "api key not valid" in low
        or "invalid api key" in low
        or "api_key_invalid" in low
    )


def mark_auth_error(error_msg: str) -> bool:
    """Records a failure, poisoning the auth cache only if it is truly an auth failure.

    Returns whether the error was treated as an authentication problem, so callers
    can distinguish "credentials are broken" from "that one call failed".
    """
    if not is_auth_error(error_msg):
        # Not a credential problem. Leave the cached auth verdict untouched so the
        # next tick is free to use Gemini again instead of being forced down the
        # unauthenticated fallback path.
        return False

    now = time.time()
    mode = get_gemini_auth_mode()
    res = {
        "mode": mode,
        "authenticated": False,
        "status": "expired",
        "error": error_msg,
        "reauth_command": "gcloud auth application-default login" if mode == "vertex_ai" else None,
        "details": error_msg,
    }
    _auth_cache["timestamp"] = now
    _auth_cache["result"] = res
    return True


def check_gemini_auth_health(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Truthfully checks if Gemini authentication credentials exist and are currently valid.
    Caches results for 5 seconds unless force_refresh is True or environment variables change.
    """
    cache_key = (
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GOOGLE_API_KEY"),
        os.environ.get("GOOGLE_CLOUD_PROJECT"),
        os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"),
    )
    now = time.time()
    if not force_refresh and _auth_cache.get("key") == cache_key and (now - _auth_cache.get("timestamp", 0) < 5.0):
        return dict(_auth_cache["result"])

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        res = {
            "mode": "api_key",
            "authenticated": True,
            "status": "valid",
            "error": None,
            "reauth_command": None,
            "details": "Using Google AI Studio API Key",
        }
        _auth_cache["key"] = cache_key
        _auth_cache["timestamp"] = now
        _auth_cache["result"] = res
        return res

    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")

    if use_vertex or project:
        try:
            import google.auth
            from google.auth.transport.requests import Request

            creds, proj = google.auth.default()
            if not creds.valid:
                try:
                    creds.refresh(Request())
                except Exception as exc:
                    err_str = str(exc)
                    res = {
                        "mode": "vertex_ai",
                        "authenticated": False,
                        "status": "expired",
                        "error": err_str,
                        "project": project or proj,
                        "reauth_command": "gcloud auth application-default login",
                        "details": f"Vertex AI ADC token expired ({err_str})",
                    }
                    _auth_cache["key"] = cache_key
                    _auth_cache["timestamp"] = now
                    _auth_cache["result"] = res
                    return res

            res = {
                "mode": "vertex_ai",
                "authenticated": True,
                "status": "valid",
                "error": None,
                "project": project or proj,
                "reauth_command": None,
                "details": f"Vertex AI ADC credentials valid (Project: {project or proj})",
            }
            _auth_cache["key"] = cache_key
            _auth_cache["timestamp"] = now
            _auth_cache["result"] = res
            return res

        except Exception as exc:
            err_str = str(exc)
            res = {
                "mode": "vertex_ai",
                "authenticated": False,
                "status": "missing_credentials",
                "error": err_str,
                "project": project,
                "reauth_command": "gcloud auth application-default login",
                "details": f"Vertex AI credentials error: {err_str}",
            }
            _auth_cache["key"] = cache_key
            _auth_cache["timestamp"] = now
            _auth_cache["result"] = res
            return res

    res = {
        "mode": "none",
        "authenticated": False,
        "status": "unconfigured",
        "error": "No API key or Google Cloud Project configured",
        "reauth_command": None,
        "details": "No Gemini credentials configured in environment",
    }
    _auth_cache["key"] = cache_key
    _auth_cache["timestamp"] = now
    _auth_cache["result"] = res
    return res


def is_gemini_authenticated() -> bool:
    """Returns True ONLY if Gemini credentials exist AND are actively valid (not expired)."""
    return bool(check_gemini_auth_health().get("authenticated", False))


def has_gemini_credentials() -> bool:
    """Returns True if Gemini API keys or Vertex AI project credentials exist."""
    return bool(
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")
    )


def get_gemini_auth_mode() -> str:
    """Returns 'vertex_ai', 'api_key', or 'none' based on current environment."""
    if os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"):
        return "vertex_ai"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "api_key"
    return "none"


async def reset_adk_session(agent_id: str):
    """Resets the in-memory ADK conversation session for an agent."""
    session_id = f"session_{agent_id}"
    try:
        await session_service.delete_session(
            app_name="cat_agent_app", user_id="cat_commander", session_id=session_id
        )
    except Exception:
        pass


def build_decision_prompt(
    obs: Observation,
    history: Optional[List[Dict[str, Any]]] = None,
    agent_id: str = "cat-1",
    history_window_size: int = 3,
    capture: Optional[Dict[str, Any]] = None,
    commander_directive: Optional[Dict[str, Any]] = None,
) -> str:
    """Assembles the exact user-turn text handed to Gemini for one decision.

    Extracted from `query_adk_gemini` so the payload can be inspected and tested
    without a network call. `query_adk_gemini` short-circuits under pytest and
    without credentials, which previously made the prompt -- the thing the whole
    inspector exists to show -- unreachable from a test.

    Three advisory sources feed the decision, in ascending order of authority:
    the evaluator's standing orders (long horizon, published to disk), the recent
    history window, and the commander's live radio order. The commander block is
    placed LAST, immediately before the model answers, because that is the
    position a model weights most heavily -- and the reported symptom was
    precisely that commander orders were being ignored.

    When `capture` is supplied it is filled in as an out-parameter with the
    prompt broken down by contributing source. It is an out-parameter rather
    than part of the return value because three test modules mock this call
    chain and assert on its arity.
    """
    pos = obs.self.get("position", {})
    facing = obs.self.get("facing", "north")
    entities = obs.visible_entities
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]

    # Format N-tick sliding context window: recent moves and reasons.
    # The running score is included on every line on purpose -- the agent
    # determines whether an attack landed by comparing this value across
    # consecutive ticks, so a single current value would be useless.
    window_size = max(1, history_window_size)
    recent_slice = history[-window_size:] if history else []
    history_lines = []
    for h in recent_slice:
        h_obs = h.get("observation", {})
        h_tick = h.get("tick", "?")
        h_self = h_obs.get("self", {})
        h_pos = h_self.get("position", {})
        h_facing = h_self.get("facing", "north")
        h_vis = h_obs.get("visible_entities", [])
        h_score = h_obs.get("score", 0)
        h_resp = h.get("response", {})
        h_act = (h_resp.get("action") or "stay").upper()
        vis_info = f"{len(h_vis)} visible ({[m.get('id') for m in h_vis]})" if h_vis else "0 visible"
        # Whether this past tick actually ran. A locked tick's action was thrown
        # away by the engine, and rendering it identically to a real move is what
        # made the trajectory look inexplicable -- the model saw itself issue the
        # same command repeatedly while barely moving, and could not tell why.
        h_cd = int(h_self.get("action_cooldown", 0) or 0)
        h_can_act = h_self.get("can_act")
        h_locked = (h_cd > 0) if h_can_act is None else (not h_can_act)
        outcome = "REJECTED - you were locked" if h_locked else "executed"
        # Rich tactical record matching Box 5 transcript:
        # Includes posture transition, score, action outcome, target, and the
        # previous thought/perception rationale so the cat maintains pursuit
        # persistence and object permanence when turning off-axis.
        h_resp = h.get("response", {})
        h_thought = (h_resp.get("thought") or "").strip()
        h_auto = h.get("autonomous_decision", {})
        h_meta = h_auto.get("meta", {}) if isinstance(h_auto, dict) else {}
        h_target = (
            h_meta.get("target_mouse_id")
            or h_meta.get("target_id")
            or h_resp.get("target_mouse_id")
            or ""
        )
        rp = h_resp.get("resulting_posture") if isinstance(h_resp, dict) else None
        if rp and rp.get("position"):
            rp_pos = rp.get("position", {})
            rp_facing = (rp.get("facing") or "").upper()
            posture_str = f"({h_pos.get('x',0)}, {h_pos.get('y',0)}) [{h_facing.upper()}] ➔ ({rp_pos.get('x',0)}, {rp_pos.get('y',0)}) [{rp_facing}]"
        else:
            posture_str = f"Pos ({h_pos.get('x',0)}, {h_pos.get('y',0)}) facing {h_facing}"

        target_str = f" Target: {h_target}." if h_target else ""
        thought_str = f" Thought: \"{h_thought}\"" if h_thought else ""
        history_lines.append(
            f"  • Tick {h_tick}: {posture_str}. SCORE: {h_score}. Context: {vis_info}. "
            f"Move: {h_act} [{outcome}].{target_str}{thought_str}"
        )

    history_context = ""
    if history_lines:
        history_context = (
            f"--- RECENT TACTICAL CONTEXT (Last {len(history_lines)} Ticks) ---\n"
            + "\n".join(history_lines)
            + "\n\n"
        )

    # Standing notes published by the evaluator subagent. Delimiter only: how
    # much weight these carry is stated once, in actor_skills.md, under
    # "Chain of Command". Nothing instructional belongs here.
    coaching_notes = store.read_coaching_notes()
    coaching_context = ""
    if coaching_notes:
        coaching_context = (
            f"{COACH_BLOCK_HEADER}\n"
            f"{coaching_notes}\n\n"
        )

    caps = obs.capabilities or {}
    atk_range = caps.get("attack_range", 2)
    vision_rad = caps.get("vision_radius", 4)
    # The other two viewport dimensions are operator-tunable at runtime, so the
    # prompt derives its description from them instead of restating constants.
    # A prompt that says "3 either side" while the engine is running a
    # half-width of 8 is worse than saying nothing: the actor's membership test
    # is the only thing standing between it and swinging at what it cannot see.
    vision_rear = caps.get("vision_rear", 2)
    vision_half = caps.get("vision_half_width", 3)
    move_cd = caps.get("move_cooldown", 3)
    atk_cd = caps.get("attack_cooldown", 3)
    lunge_cd = caps.get("lunge_cooldown", 2)

    # Rivals only; the cat's own score is already stated on its own line.
    rivals = {k: v for k, v in (obs.scoreboard or {}).items() if k != obs.self.get("id", agent_id)}
    rival_text = ", ".join(f"{cid}: {sc}" for cid, sc in sorted(rivals.items())) if rivals else "none"

    # Lock state. The server has always sent these two fields; the prompt used to
    # drop them, so the model issued commands into a locked engine with no way to
    # know they were being discarded -- observed live as 8 consecutive EAST
    # commands that produced 2 tiles of movement. Whether this tick is executable
    # is the first thing the agent needs to know, so it leads the block.
    #
    # Wording note: the scheduler pops the previously buffered reply the moment it
    # dispatches a new observation, so what executes on a free tick is the answer
    # given on the tick before it. Every answer is therefore a standing intent
    # applied at the next opportunity, and the text says so rather than promising
    # immediate execution it cannot guarantee.
    self_cd = int(obs.self.get("action_cooldown", 0) or 0)
    self_locked_action = obs.self.get("locked_action")
    _can_act = obs.self.get("can_act")
    self_locked = (self_cd > 0) if _can_act is None else (not _can_act)
    if self_locked:
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

    # When the operator enables aim assist the server pre-computes each visible
    # entity's position in our own frame, so the dict dumped below carries
    # `forward`, `offaxis` and `in_strike_zone` next to the absolute position.
    #
    # Those keys would appear in the dump whether or not we said anything, and
    # saying nothing was not an option. Unexplained fields get treated as advice
    # to be weighed against the model's own arithmetic, and the entire reason
    # this feature exists is that the arithmetic is where we lose kills -- in
    # both directions. Measured over one game: 13 swings taken at targets the
    # model had itself computed as out of range, and 11 legal chances declined
    # at |offaxis| == 1 on the belief that a shot must be dead ahead. So the
    # text states the rule as the two inclusive comparisons, states that the
    # zone is genuinely three tiles wide, and says plainly which source wins.
    #
    # Only rendered when the flag is on. A standing instruction to trust fields
    # that are absent is worse than no instruction at all.
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

    observation_context = (
        f"--- CURRENT OBSERVATION (Tick {obs.tick}, Round {obs.round}) ---\n"
        f"Agent ID: {obs.self.get('id', agent_id)}\n"
        f"Action State: {lock_text}\n"
        f"Current Position: (x={pos.get('x', 0)}, y={pos.get('y', 0)})\n"
        f"Current Facing: {facing}\n"
        f"Your Score: {obs.score}\n"
        f"Rival Cats: {rival_text}\n"
        f"Capabilities this tick:\n"
        f"  • attack_range: {atk_range} tiles deep, strike zone 3 tiles wide\n"
        f"  • vision_radius: {vision_rad} tiles ahead, plus {vision_rear} behind and "
        f"{vision_half} either side (a box around you, {2 * vision_half + 1} tiles wide)\n"
        f"  • move_cooldown: {move_cd} tick(s)\n"
        f"  • attack_cooldown: {atk_cd} tick(s)\n"
        f"  • lunge_cooldown: {lunge_cd} tick(s)\n"
        f"{aim_line}"
        f"Visible Entities in Viewport: {entities}\n"
        f"Legal Actions this tick: {legal}"
    )

    # The commander's live radio message. Delimiter, raw text, and two plain
    # numbers -- nothing else. How to weigh it, how to tell an order from a
    # question, and when it goes stale are all stated once in actor_skills.md.
    # Restating any of that here is what produced 100 consecutive lunges from
    # a message that was only ever a question.
    commander_context = ""
    commander_is_fresh = True
    directive_text = (commander_directive or {}).get("directive") or ""
    if directive_text.strip():
        issued_tick = (commander_directive or {}).get("tick")
        if isinstance(issued_tick, int):
            age = max(0, obs.tick - issued_tick)
            issued_line = f"Sent at tick {issued_tick}; {age} tick(s) ago.\n"
            commander_is_fresh = age <= COMMANDER_FRESH_TICKS
        else:
            issued_line = ""
        commander_context = (
            f"\n{COMMANDER_BLOCK_HEADER}\n"
            f"{issued_line}"
            f'COMMANDER: "{directive_text.strip()}"\n'
        )

    # Blocks are emitted in ascending order of influence, because position is
    # the only lever the prompt has: the model answers immediately after the
    # final token, so the last block read is the one nearest the decision.
    #
    # history -> coach -> observation -> commander
    #
    # The coach used to lead, which put ~10,400 characters between its
    # directives and the answer while the history window -- five times its size
    # and made of worked examples of the behaviour being corrected -- sat
    # closer. Swapping the two costs nothing and reverses that.
    #
    # A stale order decays by *position* rather than being dropped or labelled
    # stale. Suppressing it would destroy information the commander chose to
    # send, and stamping a "this is stale" verdict on it would be precisely the
    # pre-chewed conclusion this prompt refuses to draw -- the age in ticks is
    # already stated, and judging it is the model's job. Sliding it from the
    # most salient slot to the least lets it fade without either.
    if commander_is_fresh:
        prompt = f"{history_context}{coaching_context}{observation_context}{commander_context}"
    else:
        prompt = f"{commander_context}{history_context}{coaching_context}{observation_context}"


    if capture is not None:
        # Recorded as separate sections rather than one blob so the
        # workbench can show which source contributed what -- and, just as
        # importantly, which source contributed nothing on this tick.
        capture.update({
            "built_at_tick": obs.tick,
            "model": getattr(adk_agent, "model", "unknown"),
            "agent_id": agent_id,
            "history_window_size": window_size,
            # Stamped per prompt, not just per run. The budget is adjustable
            # mid-game, so a run_config written at startup cannot say which
            # setting produced any particular decision -- and a prompt log that
            # straddles a change is otherwise indistinguishable from one that
            # does not.
            "thinking_budget": get_thinking_budget(),
            "sections": {
                "system_instruction": {
                    "label": "Skills manual (actor_skills.md)",
                    "delivered": True,
                    "text": get_skills_instruction(),
                },
                "coaching_notes": {
                    "label": "Coach: standing orders from the evaluator",
                    "delivered": bool(coaching_notes),
                    "text": coaching_context or "(no coaching notes published yet)",
                },
                "commander_directive": {
                    "label": "Commander: live order from the radio",
                    "delivered": bool(commander_context),
                    "text": commander_context or (
                        "No standing order. The commander has not radioed one, or "
                        "cancelled the last one. This is an empty radio, not a "
                        "broken one."
                    ),
                },
                "history_window": {
                    "label": f"Recent tactical context (last {len(history_lines)} ticks)",
                    "delivered": bool(history_context),
                    "text": history_context or "(no history yet)",
                },
                "current_observation": {
                    "label": "Current observation",
                    "delivered": True,
                    "text": observation_context,
                },
            },
            "full_prompt": prompt,
        })

    return prompt


async def query_adk_gemini(
    obs: Observation,
    history: Optional[List[Dict[str, Any]]] = None,
    agent_id: str = "cat-1",
    history_window_size: int = 3,
    capture: Optional[Dict[str, Any]] = None,
    commander_directive: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[CatTacticalDecision], str, Optional[str]]:
    """
    Queries Gemini via Google ADK using the skills handbook.

    `commander_directive` is the human operator's standing radio order, shaped
    `{"directive": str, "tick": int}`. Its absence from this signature was the
    reason radio orders never influenced a decision: they reached only the chat
    reply, never the tick loop.

    Returns:
      (decision, engine_source, engine_error)
      - decision: CatTacticalDecision if successful, or None on failure/disabled.
      - engine_source: 'adk_gemini' on success, 'heuristic_fallback' otherwise.
      - engine_error: Error message string if an exception occurred, or None.
    """
    # Skip live network calls during automated pytest runs unless explicitly enabled
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        return None, "heuristic_fallback", None

    if not has_gemini_credentials():
        return None, "heuristic_fallback", None

    user_id = "cat_commander"
    session_id = f"session_{agent_id}"

    async with _llm_lock:
        try:
            # Refresh session to prevent token explosion across ticks
            try:
                await session_service.delete_session(
                    app_name="cat_agent_app", user_id=user_id, session_id=session_id
                )
            except Exception:
                pass
            await session_service.create_session(
                app_name="cat_agent_app", user_id=user_id, session_id=session_id
            )

            legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]
            prompt = build_decision_prompt(
                obs,
                history=history,
                agent_id=agent_id,
                history_window_size=history_window_size,
                capture=capture,
                commander_directive=commander_directive,
            )

            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            decision = None
            # What the model actually billed for thinking on this call. The
            # budget is a ceiling, not a spend: at AUTO especially, the only way
            # to know whether the cat thought at all is to read it back. Summed
            # across events because a single decision can arrive as several.
            thoughts_tokens: Optional[int] = None
            _started = time.monotonic()

            async with asyncio.timeout(get_deliberation_timeout()):
                agen = adk_runner.run_async(user_id=user_id, session_id=session_id, new_message=msg)
                try:
                    while True:
                        event = await agen.__anext__()
                        if event.error_code or event.error_message:
                            raise RuntimeError(f"ADK Error: {event.error_code} - {event.error_message}")
                        usage = getattr(event, "usage_metadata", None)
                        spent = getattr(usage, "thoughts_token_count", None) if usage else None
                        if spent is not None:
                            thoughts_tokens = (thoughts_tokens or 0) + int(spent)
                        if event.actions and event.actions.state_delta and "decision" in event.actions.state_delta:
                            decision = event.actions.state_delta["decision"]
                        elif event.content and event.content.parts:
                            for p in event.content.parts:
                                if p.text and not getattr(p, "thought", False):
                                    try:
                                        decision = CatTacticalDecision.model_validate_json(p.text)
                                    except Exception:
                                        pass
                except StopAsyncIteration:
                    pass

            if isinstance(decision, dict):
                decision = CatTacticalDecision(**decision)

            if capture is not None:
                capture["latency_ms"] = round((time.monotonic() - _started) * 1000)
                # None means the response carried no usage metadata, which is
                # not the same as zero thinking. Left distinguishable on
                # purpose: "we did not measure" must not read as "it did not
                # think" in an experiment about exactly that.
                capture["thoughts_token_count"] = thoughts_tokens

            # Every exit below reports *why* it gave up. Returning a bare None here
            # was what made fallbacks undiagnosable: the UI could only say "not
            # Gemini" and guessed at the cause, invariably blaming auth.
            if decision and decision.action:
                chosen_clean = decision.action.strip().lower()
                if chosen_clean in legal:
                    decision.action = chosen_clean
                    if capture is not None:
                        capture["decision"] = {
                            "action": decision.action,
                            "thought": decision.thought,
                            "strategy": decision.strategy,
                            "target_mouse_id": decision.target_mouse_id,
                        }
                    return decision, "adk_gemini", None
                else:
                    reason = (
                        f"model chose '{chosen_clean}', which is not legal this tick "
                        f"(legal: {legal})"
                    )
                    print(f"[ADK Warning] {reason}", flush=True)
                    return None, "heuristic_fallback", reason

        except asyncio.TimeoutError:
            # Names the thinking budget too: once it is adjustable, overrunning
            # this ceiling is the expected consequence of having raised it, and
            # a reason that omits the cause sends you looking at the network.
            reason = (
                f"Gemini call exceeded the {get_deliberation_timeout():g}s deliberation "
                f"budget (thinking budget: "
                f"{describe_thinking_budget(get_thinking_budget())})"
            )
            print(f"[ADK Timeout] {reason} (falling back to heuristics)", flush=True)
            return None, "heuristic_fallback", reason

        except Exception as exc:
            err_msg = str(exc)
            mark_auth_error(err_msg)
            print(f"[ADK Error] Gemini execution failure: {err_msg} (falling back to heuristics)", flush=True)
            return None, "heuristic_fallback", err_msg

    return None, "heuristic_fallback", "model returned no parseable decision"


async def interrogate_adk_gemini(
    user_prompt: str,
    obs: Optional[Observation] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    agent_id: str = "cat-1",
    history_window_size: int = 3,
    active_directive: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, Optional[str]]:
    """
    Queries Gemini directly for user-initiated plain text interrogation / commander questions.

    Returns:
      (reply_text, engine_source, error_message)
    """
    pos = obs.self.get("position", {}) if obs else {}
    facing = obs.self.get("facing", "north") if obs else "north"
    tick = obs.tick if obs else 0
    round_num = obs.round if obs else 0
    # `or {}` / `or []` matter here, not just the `if obs` guard: these fields are
    # Optional on the model and default to None. A radio message sent while the
    # latest observation carried no capabilities crashed this endpoint outright
    # on `caps.get(...)`.
    entities = (obs.visible_entities or []) if obs else []
    caps = (obs.capabilities or {}) if obs else {}
    legal = (obs.legal_actions or []) if obs else []

    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        return (
            f"Commander! At Tick #{tick} (x={pos.get('x',0)}, y={pos.get('y',0)}) facing {facing}. "
            f"Regarding '{user_prompt}': I am scanning with my {caps.get('vision_radius', 4)}-tile vision cone. "
            f"I have {len(entities)} entities in sight and {len(history or [])} ticks of history recorded.",
            "adk_gemini",
            None,
        )

    if not has_gemini_credentials():
        return (
            f"Commander, my Gemini neural link is offline (running local heuristic reflexes). "
            f"At Tick #{tick}, I am at position ({pos.get('x',0)}, {pos.get('y',0)}) facing {facing}. "
            f"Regarding your question ('{user_prompt}'): I am currently following basic reflex heuristics. "
            f"To unlock full cognitive interrogation, enter your Gemini API key in the workbench toolbar!",
            "heuristic_fallback",
            None,
        )

    user_id = "cat_commander"
    session_id = f"interrogate_{agent_id}"

    async with _llm_lock:
        try:
            try:
                await session_service.delete_session(
                    app_name="cat_agent_app", user_id=user_id, session_id=session_id
                )
            except Exception:
                pass
            await session_service.create_session(
                app_name="cat_agent_app", user_id=user_id, session_id=session_id
            )

            window_size = max(1, history_window_size)
            recent_slice = history[-window_size:] if history else []
            history_lines = []
            for h in recent_slice:
                h_tick = h.get("tick", "?")
                h_self = h.get("observation", {}).get("self", {})
                h_pos = h_self.get("position", {})
                h_facing = h_self.get("facing", "north")
                h_vis = h.get("observation", {}).get("visible_entities", [])
                h_resp = h.get("response", {})
                h_act = (h_resp.get("action") or "stay").upper()
                h_thought = h_resp.get("thought", "")
                vis_info = f"{len(h_vis)} visible ({[m.get('id') for m in h_vis]})" if h_vis else "0 visible"
                h_cd = int(h_self.get("action_cooldown", 0) or 0)
                h_can_act = h_self.get("can_act")
                h_locked = (h_cd > 0) if h_can_act is None else (not h_can_act)
                outcome = "REJECTED - you were locked" if h_locked else "executed"
                history_lines.append(
                    f"  • Tick {h_tick}: Pos ({h_pos.get('x',0)}, {h_pos.get('y',0)}) facing {h_facing}. "
                    f"Context: {vis_info}. Move: {h_act} [{outcome}] (Reason: \"{h_thought}\")"
                )

            history_context = ""
            if history_lines:
                history_context = (
                    f"--- RECENT TACTICAL CONTEXT (Last {len(history_lines)} Ticks) ---\n"
                    + "\n".join(history_lines)
                    + "\n\n"
                )

            directive_context = ""
            if active_directive:
                d_thought = active_directive.get("thought") or active_directive.get("directive") or ""
                d_strat = active_directive.get("strategy") or "ROAMING"
                directive_context = f"Current Tactical Phase: {d_strat}\nActive Directive: {d_thought}\n\n"

            prompt = (
                f"{history_context}"
                f"--- CURRENT OBSERVATION (Tick {tick}, Round {round_num}) ---\n"
                f"Agent ID: {agent_id}\n"
                f"Current Position: (x={pos.get('x', 0)}, y={pos.get('y', 0)})\n"
                f"Current Facing: {facing}\n"
                f"vision_radius: {caps.get('vision_radius', 4)} tiles ahead, plus "
                f"{caps.get('vision_rear', 2)} behind and {caps.get('vision_half_width', 3)} either side\n"
                f"attack_range: {caps.get('attack_range', 2)} tiles\n"
                f"Visible Entities in Viewport: {entities}\n"
                f"Legal Actions available: {legal}\n"
                f"{directive_context}"
                f"{COMMANDER_BLOCK_HEADER}\n"
                f'COMMANDER: "{user_prompt}"\n'
            )

            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            reply_parts = []

            async with asyncio.timeout(12.0):
                agen = adk_interrogator_runner.run_async(
                    user_id=user_id, session_id=session_id, new_message=msg
                )
                try:
                    while True:
                        event = await agen.__anext__()
                        if event.error_code or event.error_message:
                            raise RuntimeError(f"ADK Error: {event.error_code} - {event.error_message}")
                        if event.content and event.content.parts:
                            for p in event.content.parts:
                                if p.text and not getattr(p, "thought", False):
                                    reply_parts.append(p.text)
                except StopAsyncIteration:
                    pass

            reply_text = "".join(reply_parts).strip()
            if reply_text:
                return reply_text, "adk_gemini", None
            return "Commander, I received your transmission. Standing by at my current coordinates.", "adk_gemini", None

        except Exception as exc:
            err_msg = str(exc)
            print(f"[ADK Interrogate Error] {err_msg}", flush=True)
            return (
                f"Commander, radio interference interrupted my cognitive link: {err_msg}. "
                f"I am at ({pos.get('x',0)}, {pos.get('y',0)}) facing {facing}.",
                "heuristic_fallback",
                err_msg,
            )

