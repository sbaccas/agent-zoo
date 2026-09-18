import asyncio
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
adk_agent = LlmAgent(
    name="cat_tactician",
    model="gemini-2.5-flash",
    instruction=_actor_instruction_provider,
    output_schema=CatTacticalDecision,
    output_key="decision",
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    ),
)
session_service = InMemorySessionService()
adk_runner = Runner(
    agent=adk_agent,
    app_name="cat_agent_app",
    session_service=session_service,
)

# Initialize Dedicated Interrogation / Commander Radio Agent & Runner (Free-form conversational reasoning)
adk_interrogator_agent = LlmAgent(
    name="cat_interrogator",
    model="gemini-2.5-flash",
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
    return {
        "name": getattr(agent, "name", None),
        "model": getattr(agent, "model", None),
        "thinking_budget": getattr(thinking, "thinking_budget", None),
        "include_thoughts": getattr(thinking, "include_thoughts", None),
        "temperature": getattr(cfg, "temperature", None),
        "max_output_tokens": getattr(cfg, "max_output_tokens", None),
        "output_schema": getattr(schema, "__name__", None),
        "tools": sorted(
            getattr(t, "__name__", str(t)) for t in (getattr(agent, "tools", None) or [])
        ),
    }


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
        # Deliberately excludes the thought that produced this action. Measured
        # over 277 live prompts, the cat chose the modal action of its own
        # history window 51.5% of the time against a 16.7% chance baseline
        # (free ticks, executed-only, so the action lock cannot explain it).
        # Replaying its own reasoning turned the window into 25 worked examples
        # arguing for the behaviour the coach was simultaneously trying to
        # correct -- and the reasoning outweighed the coaching notes four to
        # one by volume. What happened is evidence; why it seemed sensible at
        # the time is advocacy, and it is dropped.
        #
        # The interrogator prompt keeps its thoughts on purpose: that agent is
        # asked to explain itself to the commander, so its past reasoning is
        # the subject matter rather than a distraction.
        history_lines.append(
            f"  • Tick {h_tick}: Pos ({h_pos.get('x',0)}, {h_pos.get('y',0)}) facing {h_facing}. "
            f"SCORE: {h_score}. Context: {vis_info}. Move: {h_act} [{outcome}]"
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
        f"  • vision_radius: {vision_rad} tiles\n"
        f"  • move_cooldown: {move_cd} tick(s)\n"
        f"  • attack_cooldown: {atk_cd} tick(s)\n"
        f"  • lunge_cooldown: {lunge_cd} tick(s)\n"
        f"Visible Entities in Vision Cone: {entities}\n"
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
            _started = time.monotonic()

            async with asyncio.timeout(8.0):
                agen = adk_runner.run_async(user_id=user_id, session_id=session_id, new_message=msg)
                try:
                    while True:
                        event = await agen.__anext__()
                        if event.error_code or event.error_message:
                            raise RuntimeError(f"ADK Error: {event.error_code} - {event.error_message}")
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
            reason = "Gemini call exceeded the 8s deliberation budget"
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
                f"vision_radius: {caps.get('vision_radius', 4)} tiles (90° forward cone)\n"
                f"attack_range: {caps.get('attack_range', 2)} tiles\n"
                f"Visible Entities in Vision Cone: {entities}\n"
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

