from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional
import uvicorn
import websockets
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import httpx

# Shared models
from actors.models import (
    ActionResponse,
    ApiKeyRequest,
    CatTacticalDecision,
    ConnectServerRequest,
    EvalIntervalRequest,
    EvalThinkingBudgetRequest,
    HistoryWindowRequest,
    GeneralInterrogateRequest,
    GeneralInterrogateResponse,
    InterrogateRequest,
    InterrogateResponse,
    Observation,
    OverrideRequest,
    ThinkingBudgetRequest,
)

# Heuristic tactical engine
from actors.cat.heuristic import (
    compute_heuristic_decision,
    compute_reflex_decision,
    compute_resulting_posture,
)

# Model / LLM tactical engine
from actors.cat.actor import (
    ACTOR_MODEL,
    COMMANDER_FRESH_TICKS,
    DELIBERATION_TIMEOUT_MAX_SEC,
    DELIBERATION_TIMEOUT_MIN_SEC,
    SKILLS_PATH,
    THINKING_BUDGET_AUTO,
    THINKING_BUDGET_MAX,
    THINKING_BUDGET_MIN,
    THINKING_BUDGET_OFF,
    THINKING_CAN_BE_DISABLED,
    adk_agent,
    adk_interrogator_agent,
    adk_runner,
    affordable_thinking_tokens,
    check_gemini_auth_health,
    coerce_deliberation_timeout,
    coerce_thinking_budget,
    describe_thinking_budget,
    get_deliberation_timeout,
    get_gemini_auth_mode,
    get_skills_instruction,
    get_thinking_budget,
    has_gemini_credentials,
    interrogate_adk_gemini,
    is_auth_error,
    is_gemini_authenticated,
    mark_auth_error,
    query_adk_gemini as model_query_adk_gemini,
    reset_adk_session,
    session_service,
    set_deliberation_timeout,
    set_thinking_budget,
    suggested_deliberation_timeout,
    summarize_agent,
    thinking_budget_warning,
)

# Runtime transcript / coaching-notes store (shared with the evaluator subagent)
from actors.cat import store

# Long-horizon evaluator subagent
from actors.cat.subagents.eval import (
    DEFAULT_EVAL_INTERVAL,
    EVAL_MODEL,
    EVAL_SKILLS_PATH,
    EVAL_THINKING_CAN_BE_DISABLED,
    EVAL_THINKING_MAX,
    EVAL_THINKING_MIN,
    EVAL_TIMEOUT_MAX_SEC,
    EVAL_TIMEOUT_MIN_SEC,
    GENERAL_RADIO_SKILLS_PATH,
    GENERAL_RADIO_TIMEOUT_SECONDS,
    clear_coach_directive,
    coerce_eval_thinking_budget,
    coerce_eval_timeout,
    eval_affordable_thinking_tokens,
    eval_agent,
    eval_thinking_warning,
    evaluate_and_publish,
    general_agent,
    get_coach_directive,
    get_eval_instruction,
    get_eval_thinking_budget,
    get_eval_timeout,
    get_general_radio_instruction,
    get_last_eval_telemetry,
    interrogate_general,
    set_coach_directive,
    set_eval_thinking_budget,
    set_eval_timeout,
    suggested_eval_timeout,
)


logger = logging.getLogger("cat_agent")

# How often the evaluation worker checks for queued work.
EVAL_WORKER_POLL_SECONDS = 1.0

# Backwards compatibility alias for heuristic decision function
compute_autonomous_decision = compute_heuristic_decision


class AgentWorkbenchState:
    """In-memory state and history store for the Cat Agent."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self.history_window_size: int = 25
        self.agent_id: str = "cat-1"
        self.simulation_id: Optional[str] = None
        self.active_tick: int = 0
        self.total_ticks_processed: int = 0
        self.latest_observation: Optional[Dict[str, Any]] = None
        self.latest_response: Optional[Dict[str, Any]] = None
        self.latest_autonomous: Optional[Dict[str, Any]] = None
        self.pending_override: Optional[str] = None
        self.override_mode: str = "once"  # "once" or "sticky"
        self.history: List[Dict[str, Any]] = []
        self.chat_history: List[Dict[str, Any]] = []
        # The General Radio log -- the General's conversation with the Coach.
        # Its own list, not a `channel` field on `chat_history`, because the two
        # transcripts answer different questions and the UI renders them in
        # different boxes. Sharing one list would mean every reader had to filter
        # correctly, and the first one that forgot would show the cat's answers
        # attributed to the Coach.
        self.general_chat_history: List[Dict[str, Any]] = []
        self.brain_engine: str = "Google ADK (gemini-2.5-flash)"
        self.skills_file: str = str(SKILLS_PATH)
        initial_health = check_gemini_auth_health()
        self.last_engine_source: str = "adk_gemini" if initial_health.get("authenticated") else "heuristic_fallback"
        self.last_engine_error: Optional[str] = initial_health.get("error") if not initial_health.get("authenticated") else None
        # Why the most recent tick did not use Gemini. Distinct from
        # `last_engine_error`: a rejected action is a fallback reason but not an
        # error, and without it the UI can only guess (it used to blame auth).
        self.last_fallback_reason: Optional[str] = None
        self.last_evaluated_tick: Optional[int] = None
        # The last tick the game server actually reported. Tracked separately from
        # `history[-1]["tick"]` because the poller advances its own tick past the
        # server's while the game is paused; comparing against the workbench's own
        # invented tick made it detect a "rewind" that never happened.
        self.last_server_tick: Optional[int] = None

        # Decoupled Deliberation & System 1/2 Architecture (AI-02)
        self.active_directive: Optional[Dict[str, Any]] = None
        # The human commander's standing radio order, deliberately kept in its OWN
        # slot. It used to share `active_directive` with the model, but
        # `_run_background_deliberation` overwrites that slot with Gemini's own
        # decision every time it runs, so a commander order survived a tick or two
        # at most. That single shared slot was the reason radio orders appeared to
        # be ignored. Shaped `{"directive": str, "tick": int, "issued_at": str}`.
        self.commander_directive: Optional[Dict[str, Any]] = None
        self.deliberation_task: Optional[asyncio.Task] = None
        self.latest_deliberated_decision: Optional[CatTacticalDecision] = None
        # Travels with the decision above. A background deliberation builds its
        # prompt during one tick but the decision is consumed on a later one, so
        # the prompt cannot be logged until we know which tick actually used it.
        self.latest_deliberated_prompt: Optional[Dict[str, Any]] = None
        self.is_deliberating: bool = False

        # Reflexion loop: long-horizon evaluator subagent.
        # `eval_interval` is how many ticks of play accumulate before the transcript
        # is pushed to the evaluator. Adjustable at runtime via the API.
        self.eval_interval: int = DEFAULT_EVAL_INTERVAL
        self.eval_enabled: bool = True
        self.last_eval_dispatch_tick: int = 0
        self.eval_pending: bool = False
        self.eval_worker_task: Optional[asyncio.Task] = None
        self.is_evaluating: bool = False
        self.last_eval_result: Optional[Dict[str, Any]] = None

        # Every change to the actor's thinking budget, stamped with the tick it
        # took effect on.
        #
        # The budget itself is deliberately NOT stored here -- `actor` owns it
        # and `get_thinking_budget()` reads it off the live agent, so there is
        # no second copy to drift. What is stored is the *history*, which the
        # agent object cannot know.
        #
        # This exists because the setting is adjustable mid-game. A run_config
        # written once at startup would describe a run that raised its budget at
        # tick 300 exactly as it describes one that never touched it, and an
        # archive that cannot tell those apart is worse than no archive: it
        # invites a comparison between two runs where only one was controlled.
        self.thinking_budget_changes: List[Dict[str, Any]] = []

        # The same history, for the evaluator subagent. Kept as a separate list
        # rather than a `who` field on the one above because the two answer
        # different questions about a run: the actor's budget explains a box
        # score, the evaluator's explains the standing orders the actor was
        # playing under. Merging them would force every reader to filter before
        # either question could be asked.
        self.eval_thinking_budget_changes: List[Dict[str, Any]] = []


# Singleton in-memory state
state = AgentWorkbenchState()


def _record_prompt(
    bundle: Optional[Dict[str, Any]],
    applied_at_tick: Optional[int],
    source: str,
) -> None:
    """Persists one captured prompt, stamped with the tick that consumed it.

    `bundle` is empty when the model was never actually called (no credentials,
    a pytest run, or a mocked query), so there is nothing to record.
    """
    if not bundle or applied_at_tick is None:
        return
    record = dict(bundle)
    record["applied_at_tick"] = int(applied_at_tick)
    record["source"] = source
    record["recorded_at"] = datetime.now(timezone.utc).isoformat()
    store.append_prompt(record)


async def query_adk_gemini(obs: Observation) -> Optional[CatTacticalDecision]:
    """Wraps model execution, updating agent workbench diagnostic state."""
    bundle: Dict[str, Any] = {}
    decision, source, reason = await model_query_adk_gemini(
        obs,
        history=state.history,
        agent_id=state.agent_id,
        history_window_size=state.history_window_size,
        capture=bundle,
        commander_directive=state.commander_directive,
    )
    state.last_engine_source = source
    if decision is not None:
        # A success clears prior diagnostics. Anything else preserves the reason so
        # it survives long enough to be displayed.
        state.last_engine_error = None
        state.last_fallback_reason = None
        # Synchronous path: built and applied on the same tick.
        _record_prompt(bundle, obs.tick, source)
    else:
        state.last_fallback_reason = reason
        # Only surface it as an engine *error* if it really is one; a rejected
        # action is an expected outcome, not a malfunction.
        state.last_engine_error = reason if is_auth_error(reason or "") else None
    return decision


def run_sync(coro):
    """Executes a coroutine synchronously, handling existing running event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


async def _run_background_deliberation(obs: Observation):
    """Executes Gemini LLM macro-deliberation in the background without stalling the game clock."""
    try:
        state.is_deliberating = True
        bundle: Dict[str, Any] = {}
        decision, _source, reason = await model_query_adk_gemini(
            obs,
            history=state.history,
            agent_id=state.agent_id,
            history_window_size=state.history_window_size,
            capture=bundle,
            commander_directive=state.commander_directive,
        )
        # NOTE: deliberately does NOT set `state.last_engine_source`.
        #
        # This runs detached from the tick loop and produces a decision for a
        # FUTURE tick. Labelling the engine here overwrote the source of the tick
        # that was actually just decided -- observed live as
        # `last_engine_source='adk_gemini'` while the only recorded row was
        # `heuristic_reflex`. Only `act_async` may attribute a decision, because
        # only it knows which decision was actually executed.
        if decision:
            state.last_engine_error = None
            state.last_fallback_reason = None
            state.latest_deliberated_decision = decision
            # Logged by act_async, not here: only the consuming tick knows the
            # `applied_at_tick` this prompt should be filed under.
            state.latest_deliberated_prompt = bundle or None
            state.active_directive = {
                "strategy": decision.strategy,
                "target_mouse_id": decision.target_mouse_id,
                "thought": decision.thought,
                "action": decision.action,
                "tick": obs.tick,
            }
        else:
            state.last_fallback_reason = reason
            # `mark_auth_error` now ignores anything that is not a credential
            # problem, so a timeout here no longer disables Gemini for 5 seconds.
            if reason and mark_auth_error(reason):
                state.last_engine_error = reason
            else:
                state.last_engine_error = None
    except Exception as exc:
        err_msg = str(exc)
        # Same reasoning: a failed background call does not change how the last
        # tick was decided. The next tick will fall back and label itself.
        state.last_fallback_reason = err_msg
        state.last_engine_error = err_msg if mark_auth_error(err_msg) else None
    finally:
        state.is_deliberating = False


async def _run_evaluation_cycle():
    """Pushes the transcript to the evaluator subagent and stores the result.

    Runs detached from the game loop: an evaluation reads a long transcript and can
    take several seconds, which must never stall tick processing.
    """
    try:
        state.is_evaluating = True
        result = await evaluate_and_publish()
        state.last_eval_result = result
    except Exception as exc:
        logger.warning("Evaluation cycle failed: %s", exc)
    finally:
        state.is_evaluating = False


async def _evaluation_worker():
    """Long-lived worker that drains evaluation requests.

    Owned by the app lifespan rather than by a request handler. Tick processing
    arrives on two different loops -- the uvicorn loop for HTTP `/act`, and a
    throwaway loop for the scheduler's synchronous `act()` -- so a task spawned at
    the point of dispatch is not guaranteed to survive long enough to publish.
    Raising a flag and letting this worker do the work is safe from both.
    """
    logger.info("Evaluation worker started.")
    while True:
        try:
            await asyncio.sleep(EVAL_WORKER_POLL_SECONDS)
            if state.eval_pending and not state.is_evaluating:
                state.eval_pending = False
                await _run_evaluation_cycle()
        except asyncio.CancelledError:
            logger.info("Evaluation worker stopped.")
            raise
        except Exception as exc:
            # A worker that dies takes the whole reflexion loop with it.
            logger.warning("Evaluation worker error: %s", exc)


def dispatch_evaluation() -> bool:
    """Requests an evaluation. Returns False if one is already queued or running.

    Setting a flag is the stand-in for a pub/sub publish. When that lands, only
    this function changes: the evaluator already communicates purely through the
    transcript and coaching-notes files and never reaches back into the actor.
    """
    if state.is_evaluating or state.eval_pending:
        return False
    state.eval_pending = True
    return True


def maybe_dispatch_evaluation(current_tick: int) -> None:
    """Requests an evaluation once the configured tick interval has elapsed."""
    if not state.eval_enabled:
        return
    interval = max(1, state.eval_interval)
    if current_tick - state.last_eval_dispatch_tick < interval:
        return
    # Record the attempt regardless of whether dispatch succeeds, so a busy
    # evaluator cannot cause a retry storm on every subsequent tick.
    state.last_eval_dispatch_tick = current_tick
    if dispatch_evaluation():
        logger.info(
            "Queued evaluation at tick %s (interval=%s)", current_tick, interval
        )


def reset_reflexion_state(current_tick: Optional[int] = None) -> None:
    """Wipes the transcript and coaching notes. Called whenever the game resets.

    Both must go together: a fresh game must not inherit the previous game's
    standing orders, and the evaluator must never read a transcript that spans two
    unrelated games.

    `current_tick` rebases the evaluation clock. Leaving it at 0 meant the very
    next tick satisfied `tick - 0 >= interval` for any tick past the interval, so
    a reset late in a game immediately fired an evaluation against a transcript
    holding a single record -- and those one-tick conclusions became the cat's
    standing orders.
    """
    store.reset_transcript()
    store.reset_coaching_notes()
    # The prompt log is scoped to a single game for the same reason as the
    # transcript, and it grows far faster, so it must not survive a reset.
    store.reset_prompts()
    state.latest_deliberated_prompt = None
    # The commander's standing order goes too. It names positions and mouse IDs
    # from a world that no longer exists, and once the tick counter rewinds to 0
    # the order's stated age becomes permanently wrong. Re-radio it if it still
    # applies.
    state.commander_directive = None
    state.last_eval_dispatch_tick = int(current_tick or 0)
    state.last_eval_result = None
    # Drop any queued request; it was raised against the previous game's transcript.
    state.eval_pending = False




async def act_async(obs: Observation, decoupled: bool = False) -> ActionResponse:
    """Evaluates an observation, arbitrating between manual override, LLM, and fast reflex."""
    obs_dict = obs.model_dump()
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]

    active_dir = state.active_directive or {}
    locked_target = active_dir.get("target_mouse_id") or None
    active_strat = active_dir.get("strategy") or "ROAMING"

    auth_health = check_gemini_auth_health()
    is_authenticated = bool(auth_health.get("authenticated", False))
    # Deliberately NOT clearing `state.last_engine_error` here. Healthy credentials
    # say nothing about whether the last call succeeded, and wiping it on every
    # tick destroyed the only record of why a fallback happened.
    auth_err = None if is_authenticated else (auth_health.get("error") or state.last_engine_error)
    fallback_reason: Optional[str] = None

    if decoupled and is_authenticated:
        # System 1 / System 2 Decoupled Deliberation (AI-02):
        # 1. Did a background Gemini deliberation task just complete?
        if state.latest_deliberated_decision and state.latest_deliberated_decision.action in legal:
            llm_decision = state.latest_deliberated_decision
            state.latest_deliberated_decision = None
            # This is the moment the applied tick becomes known. The prompt was
            # built during an earlier tick, which is why the record carries both
            # `built_at_tick` and `applied_at_tick`. Clear it either way so a
            # subsequent reflex tick cannot inherit a stale prompt.
            _record_prompt(state.latest_deliberated_prompt, obs.tick, "adk_gemini")
            state.latest_deliberated_prompt = None
            auto_action = llm_decision.action
            auto_thought = llm_decision.thought
            auto_meta = {
                "engine": "adk_gemini",
                "strategy": llm_decision.strategy,
                "target_mouse_id": llm_decision.target_mouse_id,
                "phase": llm_decision.strategy,
            }
            state.last_engine_source = "adk_gemini"
        else:
            # 2. Fast Reflex System 1 (Strike-Zone pounce / target-lock pursuit / roaming)
            #
            # This is a heuristic decision and is now labelled as one. It used to
            # report `adk_gemini`, which meant most WebSocket ticks were credited
            # to the model even though the model had not answered yet.
            auto_action, auto_thought, auto_meta = compute_reflex_decision(
                obs, locked_target_id=locked_target, strategy=active_strat
            )
            auto_meta["engine"] = "heuristic_reflex"
            auto_meta["strategy"] = active_strat
            auto_meta["target_id"] = locked_target
            auto_meta["phase"] = active_strat
            state.last_engine_source = "heuristic_reflex"
            fallback_reason = (
                "Gemini still deliberating; acted on System-1 reflex within tick budget"
                if state.is_deliberating
                else "no completed Gemini decision available for this tick"
            )

        # 3. Trigger background deliberation task if free
        if not state.is_deliberating and (state.deliberation_task is None or state.deliberation_task.done()):
            state.deliberation_task = asyncio.create_task(_run_background_deliberation(obs))
    else:
        # Standard synchronous path / Fallback when unauthenticated
        auto_action, auto_thought, auto_meta = compute_heuristic_decision(obs)

        if is_authenticated:
            llm_decision = await query_adk_gemini(obs)
            if llm_decision and llm_decision.action in legal:
                auto_action = llm_decision.action
                auto_thought = llm_decision.thought
                auto_meta["engine"] = "adk_gemini"
                auto_meta["strategy"] = llm_decision.strategy
                auto_meta["target_mouse_id"] = llm_decision.target_mouse_id
                auto_meta["phase"] = llm_decision.strategy
                state.last_engine_source = "adk_gemini"
                state.active_directive = {
                    "strategy": llm_decision.strategy,
                    "target_mouse_id": llm_decision.target_mouse_id,
                    "thought": llm_decision.thought,
                    "tick": obs.tick,
                }
            else:
                auto_meta["engine"] = "heuristic_fallback"
                state.last_engine_source = "heuristic_fallback"
                # Set by `query_adk_gemini`; carries the real cause (timeout,
                # rejected action, API error) rather than an assumed auth failure.
                fallback_reason = state.last_fallback_reason
        elif has_gemini_credentials() or auth_err:
            auto_meta["engine"] = "heuristic_fallback"
            state.last_engine_source = "heuristic_fallback"
            fallback_reason = auth_err or "credentials present but not currently valid"
            if not state.last_engine_error and auth_err:
                state.last_engine_error = auth_err
        else:
            auto_meta["engine"] = "heuristic"
            state.last_engine_source = "heuristic"
            fallback_reason = "no Gemini credentials configured"

    auto_meta["fallback_reason"] = fallback_reason
    state.last_fallback_reason = fallback_reason

    # 3. Check for human manual override
    chosen_action = auto_action
    chosen_thought = auto_thought
    source = auto_meta.get("engine", "heuristic")

    if state.pending_override:
        override = state.pending_override.lower()
        if override in legal:
            chosen_action = override
            chosen_thought = f"Manual override applied: {chosen_action}"
            source = "manual_override"
        else:
            chosen_thought = f"Override '{override}' illegal this tick; executed autonomous move: {auto_action}"

        if state.override_mode == "once":
            state.pending_override = None

    # Is this tick executable? Prefer the server's explicit `can_act` flag: the
    # scheduler decrements the cooldown before it builds the payload, so on the
    # final locked tick the counter reads 0 while the tick is still being
    # skipped. Fall back to the counter for callers that predate the flag.
    self_cooldown = int(obs.self.get("action_cooldown", 0) or 0)
    can_act = obs.self.get("can_act")
    if can_act is None:
        is_locked = self_cooldown > 0
    else:
        is_locked = not can_act
    tick_type = "cooldown" if is_locked else "play"

    # Calculate posture resulting from chosen action.
    #
    # On a locked tick the action is discarded by the engine, so the resulting
    # posture is simply where we already are. Projecting the move anyway made the
    # transcript claim 991 tiles of travel against a physical ceiling of ~522,
    # and that transcript is what the evaluator reads.
    pos = obs.self.get("position", {"x": 0, "y": 0})
    facing = obs.self.get("facing", "north")
    if is_locked:
        posture = {"position": dict(pos), "facing": facing}
    else:
        posture = compute_resulting_posture(pos, facing, chosen_action)

    exchange = {
        "tick": obs.tick,
        "round": obs.round,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "observation": obs_dict,
        "autonomous_decision": {
            "action": auto_action,
            "thought": auto_thought,
            "meta": auto_meta,
        },
        "response": {
            "action": chosen_action,
            "thought": chosen_thought,
            "source": source,
            "fallback_reason": fallback_reason,
            "resulting_posture": posture,
        },
        "source": source,
        # Lock dimension, kept separate from `source`. `source` answers "which
        # engine decided this"; `tick_type` answers "did the engine let it
        # happen". Overloading one field would have lost the other.
        "tick_type": tick_type,
        "locked": is_locked,
        "action_cooldown": self_cooldown,
        "locked_action": obs.self.get("locked_action"),
        "executed": not is_locked,
    }

    # Update latest observation with resulting posture to eliminate 1-tick display lag
    current_obs = dict(obs_dict)
    current_obs["self"] = dict(obs_dict.get("self", {}))
    current_obs["self"]["position"] = posture["position"]
    current_obs["self"]["facing"] = posture["facing"]
    if obs.capabilities:
        current_obs["capabilities"] = obs.capabilities
    elif "capabilities" in obs_dict:
        current_obs["capabilities"] = obs_dict["capabilities"]

    state.latest_observation = current_obs
    state.latest_response = exchange["response"]
    state.latest_autonomous = exchange["autonomous_decision"]
    state.active_tick = obs.tick
    state.last_evaluated_tick = obs.tick
    state.total_ticks_processed += 1

    state.history.append(exchange)
    if len(state.history) > state.max_history:
        state.history.pop(0)

    # The in-memory history above stays a short rolling window for the live UI.
    # The durable transcript lives on disk, so the evaluator's horizon is not
    # limited by `max_history`.
    store.append_tick(exchange)
    maybe_dispatch_evaluation(obs.tick)

    return ActionResponse(action=chosen_action, thought=chosen_thought)


def act(obs: Observation, use_llm: bool = True) -> ActionResponse:
    """Synchronous interface for scheduler fallback or direct testing."""
    if not use_llm:
        action, thought, _ = compute_heuristic_decision(obs)
        return ActionResponse(action=action, thought=thought)
    return run_sync(act_async(obs))


class WsClientManager:
    """Manages persistent outbound WebSocket connection to the Game Server."""

    def __init__(self):
        self.server_url: str = os.environ.get("GAME_SERVER_WS_URL", "ws://127.0.0.1:8000/ws/agents")
        self.requested_cat_id: Optional[str] = None
        self.connected: bool = False
        self.connecting: bool = False
        self.assigned_id: Optional[str] = None
        self.last_error: Optional[str] = None
        self.auto_reconnect: bool = True
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        # The deliberation currently in flight. Exactly one at a time: a new
        # observation supersedes whatever is still thinking, because that older
        # answer can no longer be accepted by the server anyway.
        self._deliberation: Optional[asyncio.Task] = None
        # Rolling record of how long deliberations took and how they ended.
        # Abandoned attempts are kept: a run where the model is consistently a
        # little over the budget looks identical to one where it is wildly over,
        # unless the overruns themselves are recorded.
        self.deliberation_log: List[Dict[str, Any]] = []
        self.max_deliberation_log: int = 200

    def get_status(self) -> Dict[str, Any]:
        recent = self.deliberation_log[-50:]
        answered = [d for d in recent if d["outcome"] == "answered"]
        return {
            "connected": self.connected,
            "connecting": self.connecting,
            "server_url": self.server_url,
            "assigned_id": self.assigned_id or state.agent_id or "cat-1",
            "requested_cat_id": self.requested_cat_id,
            "last_error": self.last_error,
            "auto_reconnect": self.auto_reconnect,
            # Deliberation timing over the recent window. `answered` is the only
            # outcome that reached the board; the rest are the cost of being slow.
            "deliberation": {
                "samples": len(recent),
                "answered": len(answered),
                "abandoned": len([d for d in recent if d["outcome"] == "abandoned"]),
                "superseded": len([d for d in recent if d["outcome"] == "superseded"]),
                "errors": len([d for d in recent if d["outcome"] in ("error", "send_failed")]),
                "avg_answered_ms": (
                    int(sum(d["elapsed_ms"] for d in answered) / len(answered))
                    if answered else None
                ),
                "max_answered_ms": max((d["elapsed_ms"] for d in answered), default=None),
                "recent": recent[-10:],
            },
        }

    async def connect(self, server_url: Optional[str] = None, cat_id: Optional[str] = None):
        if server_url and server_url.strip():
            url = server_url.strip()
            if url.startswith("http://"):
                url = "ws://" + url[7:]
            elif url.startswith("https://"):
                url = "wss://" + url[8:]
            if not url.startswith("ws://") and not url.startswith("wss://"):
                url = f"ws://{url}"
            if "/ws/agents" not in url:
                url = url.rstrip("/") + "/ws/agents"
            self.server_url = url

        if cat_id and cat_id.strip():
            new_id = cat_id.strip()
            if new_id != self.assigned_id:
                state.history.clear()
                reset_reflexion_state(state.active_tick)
                state.total_ticks_processed = 0
                state.latest_observation = None
                state.latest_response = None
                state.latest_autonomous = None
            self.requested_cat_id = new_id
            state.agent_id = new_id

        self.auto_reconnect = True
        self._stop_event.clear()

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._task and not self._task.done():
            self._task.cancel()

        self._task = asyncio.create_task(self._worker_loop())

    async def disconnect(self):
        self.auto_reconnect = False
        self._stop_event.set()
        # Nothing in flight can be delivered over a socket that is closing.
        self._supersede_deliberation("disconnecting")
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._task and not self._task.done():
            self._task.cancel()
        self.connected = False
        self.connecting = False

    def _record_deliberation(self, tick: Any, elapsed_ms: int, outcome: str) -> None:
        """Logs how long one deliberation took and how it ended.

        Kept on the agent rather than the game server: the server knows only
        whether an answer showed up in time, which cannot distinguish a model
        that was a fraction late from one that never had a chance.
        """
        self.deliberation_log.append({
            "tick": tick,
            "elapsed_ms": elapsed_ms,
            "outcome": outcome,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        if len(self.deliberation_log) > self.max_deliberation_log:
            del self.deliberation_log[:-self.max_deliberation_log]

    def _supersede_deliberation(self, reason: str) -> None:
        """Cancels any in-flight deliberation. Its answer could not be used now."""
        task = self._deliberation
        if task and not task.done():
            task.cancel()
        self._deliberation = None

    async def _deliberate_and_reply(
        self,
        ws,
        obs_dict: Dict[str, Any],
        budget_sec: Optional[float],
    ) -> None:
        """Thinks about one observation and answers, or gives up quietly.

        A late answer is never sent. The server would discard it anyway (it
        carries a tick the board has moved past), and the turn it was meant for
        has already been played by the heuristic. Sending it would only risk it
        being mistaken for a fresh answer by a future, laxer server.

        An error is treated the same way, deliberately. The previous behaviour
        was to send `stay` on failure, which is a real decision and a poor one;
        staying silent hands the turn to the heuristic instead, which plays it
        considerably better.
        """
        started = time.monotonic()
        tick = obs_dict.get("tick")

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            obs_obj = Observation(**obs_dict)
            coro = act_async(obs_obj, decoupled=True)
            if budget_sec and budget_sec > 0:
                action_resp = await asyncio.wait_for(coro, timeout=budget_sec)
            else:
                action_resp = await coro
        except asyncio.TimeoutError:
            self._record_deliberation(tick, elapsed_ms(), "abandoned")
            print(
                f"⏱️ [WS CLIENT] Abandoned tick {tick} after {elapsed_ms()}ms "
                f"(budget {budget_sec}s) - heuristic will cover",
                flush=True,
            )
            return
        except asyncio.CancelledError:
            self._record_deliberation(tick, elapsed_ms(), "superseded")
            raise
        except Exception as err:
            self._record_deliberation(tick, elapsed_ms(), "error")
            print(f"⚠️ [WS CLIENT] Error computing action for tick {tick}: {err}", flush=True)
            return

        meta = state.latest_autonomous.get("meta", {}) if state.latest_autonomous else {}
        try:
            await ws.send(json.dumps({
                "type": "action",
                "tick": obs_obj.tick,
                "action": action_resp.action,
                "thought": action_resp.thought,
                "strategy": meta.get("strategy", "ROAMING"),
                "target_mouse_id": meta.get("target_mouse_id", ""),
                # Which system actually chose this. `act_async(decoupled=True)`
                # never waits for Gemini: it serves a completed deliberation if
                # one is ready and otherwise answers from the System-1 reflex.
                # Both arrive at the server equally promptly, so arrival time
                # cannot distinguish them and the server would credit the model
                # for every tick the reflex played.
                "engine": meta.get("engine"),
            }))
        except Exception as err:
            self._record_deliberation(tick, elapsed_ms(), "send_failed")
            print(f"⚠️ [WS CLIENT] Could not deliver action for tick {tick}: {err}", flush=True)
            return

        self._record_deliberation(tick, elapsed_ms(), "answered")

    async def _worker_loop(self):
        while self.auto_reconnect and not self._stop_event.is_set():
            self.connecting = True
            self.connected = False
            try:
                print(f"🐾 [WS CLIENT] Connecting to Game Server at {self.server_url}...", flush=True)
                async with websockets.connect(self.server_url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    self.connecting = False
                    self.connected = True
                    self.last_error = None
                    print(f"🐾 [WS CLIENT] Connected successfully to {self.server_url}", flush=True)

                    reg_msg = {
                        "type": "register",
                        "role": "cat",
                        "agent_name": "gemini-cat",
                        "requested_id": self.requested_cat_id or state.agent_id or "cat-1",
                    }
                    await ws.send(json.dumps(reg_msg))

                    async for raw_msg in ws:
                        try:
                            data = json.loads(raw_msg)
                        except Exception:
                            continue

                        mtype = data.get("type")
                        if mtype == "registered":
                            self.assigned_id = data.get("agent_id", "cat-1")
                            state.agent_id = self.assigned_id
                            state.simulation_id = data.get("simulation_id")
                            print(f"🐾 [WS CLIENT] Handshake complete! Bound to entity '{self.assigned_id}'", flush=True)
                            init_obs = data.get("initial_observation")
                            if init_obs and isinstance(init_obs, dict) and "self" in init_obs:
                                try:
                                    obs_obj = Observation(**init_obs)
                                    await act_async(obs_obj, decoupled=True)
                                except Exception:
                                    pass

                        elif mtype == "observation":
                            # Spawned, not awaited. Awaiting here blocked the
                            # receive loop for the whole deliberation, so a slow
                            # answer pushed the next observation back behind it
                            # and the actor drifted further from the live board
                            # with every tick it was late.
                            self._supersede_deliberation("newer observation arrived")
                            self._deliberation = asyncio.create_task(
                                self._deliberate_and_reply(
                                    ws,
                                    data.get("payload", {}),
                                    data.get("budget_sec"),
                                )
                            )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = str(exc)
                self.connected = False
                self.connecting = False
                if self.auto_reconnect and not self._stop_event.is_set():
                    await asyncio.sleep(2.0)
                else:
                    break


ws_client = WsClientManager()


async def sync_live_observation(agent_id: Optional[str] = None) -> bool:
    """Fetches current observation from Game Server HTTP endpoint."""
    target_id = agent_id or state.agent_id or "cat-1"
    game_server_url = "http://127.0.0.1:8000"
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            resp = await client.get(f"{game_server_url}/api/simulations/active/agents/{target_id}/observation")
            if resp.status_code == 200:
                data = resp.json()
                sim_id = data.get("simulation_id")
                server_tick = data.get("tick", 0)

                if state.simulation_id and sim_id and state.simulation_id != sim_id:
                    state.history.clear()
                    reset_reflexion_state(server_tick)
                    state.total_ticks_processed = 0
                    state.last_evaluated_tick = None
                    state.last_server_tick = None
                    await reset_adk_session(target_id)
                state.simulation_id = sim_id

                # A genuine rewind is the GAME's tick going backwards. Compare
                # against the last tick the server itself reported, never against
                # `history[-1]`: the block below deliberately advances the
                # workbench past the server while the game is paused, so comparing
                # with local history made this branch fire on its own output and
                # destroy the entire transcript on a single poll.
                if state.last_server_tick is not None and server_tick < state.last_server_tick:
                    logger.info(
                        "Game rewound (server tick %s -> %s); resetting reflexion state.",
                        state.last_server_tick,
                        server_tick,
                    )
                    state.history.clear()
                    reset_reflexion_state(server_tick)
                    state.total_ticks_processed = 0
                    state.last_evaluated_tick = None
                    await reset_adk_session(target_id)

                state.last_server_tick = server_tick
                last_history_tick = state.history[-1]["tick"] if state.history else 0

                if state.history and last_history_tick >= server_tick:
                    upcoming_tick = last_history_tick + 1
                elif server_tick == 0 and not state.history:
                    upcoming_tick = 1
                else:
                    upcoming_tick = server_tick

                if (
                    state.latest_observation is not None
                    and state.active_tick == upcoming_tick
                    and state.latest_autonomous is not None
                    and state.last_evaluated_tick == upcoming_tick
                ):
                    return True

                self_info = data.get("self", {})
                live_obs = {
                    "tick": upcoming_tick,
                    "round": data.get("round", 0),
                    "self": {
                        "id": self_info.get("id", target_id),
                        "type": self_info.get("type", "cat"),
                        "position": self_info.get("position", {"x": 0, "y": 0}),
                        "facing": self_info.get("facing", "north"),
                        "action_cooldown": self_info.get("action_cooldown", 0),
                        "locked_action": self_info.get("locked_action"),
                        "ready_to_act": self_info.get("ready_to_act", True),
                    },
                    "capabilities": data.get("capabilities") or {
                        "vision_radius": 4,
                        "attack_range": 2,
                        "move_cooldown": 3,
                        "attack_cooldown": 3,
                        "lunge_cooldown": 2,
                        "weight": 10,
                    },
                    "visible_entities": data.get("visible_entities", []),
                    "legal_actions": data.get("legal_actions", ["stay", "north", "south", "east", "west", "attack", "lunge"]),
                    # Outcome feedback. This builder whitelists fields, so these must
                    # be forwarded explicitly or the polled path silently drops the
                    # score while the WebSocket path keeps it.
                    "score": data.get("score", 0),
                    "scoreboard": data.get("scoreboard", {}),
                }

                state.agent_id = target_id
                state.latest_observation = live_obs
                state.active_tick = upcoming_tick

                if state.latest_autonomous is None:
                    live_obs_obj = Observation(**live_obs)
                    auto_action, auto_thought, auto_meta = compute_heuristic_decision(live_obs_obj)
                    auto_meta["engine"] = "heuristic_preview"
                    state.latest_autonomous = {
                        "action": auto_action,
                        "thought": auto_thought,
                        "meta": auto_meta,
                    }
                    state.last_evaluated_tick = upcoming_tick
                return True
    except Exception:
        pass
    return False


class PollingLogFilter(logging.Filter):
    """Suppresses high-frequency access logs from workbench frontend polling."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = getattr(record, "args", ())
        if len(args) >= 5:
            # args format: (client_addr, method, path, http_version, status_code)
            path = args[2]
            status = args[4]
            if status == 200 and any(p in path for p in ["/api/agent/state", "/api/agent/server", "/favicon.ico"]):
                return False
        msg = record.getMessage()
        if (" 200 " in msg or "200 OK" in msg) and any(p in msg for p in ["/api/agent/state", "/api/agent/server", "/favicon.ico"]):
            return False
        return True


def publish_run_config() -> None:
    """Writes the settings this process is running with, for the game archive.

    Nothing reads this at runtime; the game server copies it into the archive
    when a game ends, so that a box score can later be attributed to a
    configuration rather than to a vague memory of what was being tried that
    afternoon.

    Called at startup *and* again whenever a tunable changes. It used to be a
    one-shot write in the lifespan, which was correct only while every setting
    it reported was fixed for the life of the process. The thinking budget is
    not, and a file that still claimed `thinking_budget: 0` an hour after it was
    raised would be worse than absent -- it would be confidently wrong in the
    one place set aside for recording the independent variable.
    """
    store.write_run_config(
        {
            "agents": [
                summarize_agent(adk_agent),
                summarize_agent(adk_interrogator_agent),
                summarize_agent(eval_agent),
            ],
            "prompt_assembly": {
                # Ticks after which a commander order stops being emitted last.
                "commander_fresh_ticks": COMMANDER_FRESH_TICKS,
            },
            "evaluation": {
                "enabled": state.eval_enabled,
                "interval_ticks": state.eval_interval,
                "default_interval_ticks": DEFAULT_EVAL_INTERVAL,
                # The evaluator's own abandon ceiling. Recorded for the same
                # reason the actor's is: an evaluation that overruns is replaced
                # by offline filler which is then published over the standing
                # orders, so a run where this was set too low produces coaching
                # notes no model wrote.
                "timeout_sec": get_eval_timeout(),
            },
            "deliberation": {
                # The ceiling a decision must arrive inside of, which bounds how
                # much thinking is actually affordable. Recorded next to the
                # budget because neither number means much without the other.
                "timeout_sec": get_deliberation_timeout(),
            },
            # Empty for a run that never touched the dial, which is exactly the
            # claim a controlled run needs to be able to make. Split by agent so
            # a run that moved only one of them still reads as controlled.
            "thinking_budget_changes": list(state.thinking_budget_changes),
            "eval_thinking_budget_changes": list(state.eval_thinking_budget_changes),
        }
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Suppress high-frequency polling from uvicorn.access logger and handlers
    filt = PollingLogFilter()
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(filt)
    for h in access_logger.handlers:
        h.addFilter(filt)

    publish_run_config()

    # Auto-connect WebSocket client in background if enabled
    if os.environ.get("CAT_AGENT_AUTO_CONNECT", "1") != "0":
        asyncio.create_task(ws_client.connect())

    # Long-horizon reflexion loop. Owned here so it outlives any single request.
    state.eval_worker_task = asyncio.create_task(_evaluation_worker())

    yield

    if state.eval_worker_task:
        state.eval_worker_task.cancel()
        try:
            await state.eval_worker_task
        except asyncio.CancelledError:
            pass
        state.eval_worker_task = None
    await ws_client.disconnect()


app = FastAPI(title="Cat Agent Service & Workbench", lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self' ws: wss:;"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)


@app.post("/act", response_model=ActionResponse)
@app.post("/", response_model=ActionResponse)
async def act_endpoint(obs: Observation) -> ActionResponse:
    return await act_async(obs)


@app.get("/api/agent/state")
async def get_agent_state(sync: bool = False, agent_id: Optional[str] = None) -> Dict[str, Any]:
    if sync:
        await sync_live_observation(agent_id)

    auth_health = check_gemini_auth_health()
    is_auth = bool(auth_health.get("authenticated", False))
    auth_mode = auth_health.get("mode", "none")
    # NOTE: do NOT overwrite `state.last_engine_source` / `last_engine_error` here.
    # Healthy credentials right now say nothing about how the last decision was
    # actually made. Force-setting them made every past tick claim it came from
    # Gemini the moment auth recovered (see AI-04).
    #
    # `auth_err` is strictly the *current credential* problem, if any.
    auth_err = None if is_auth else (auth_health.get("error") or state.last_engine_error)

    return {
        "connected": (state.latest_observation is not None) or ws_client.connected,
        "server_connected": ws_client.connected,
        "server_connection": ws_client.get_status(),
        "agent_id": state.agent_id,
        "active_tick": state.active_tick,
        "total_ticks_processed": state.total_ticks_processed,
        "latest_observation": state.latest_observation,
        "latest_response": state.latest_response,
        "latest_autonomous": state.latest_autonomous,
        "pending_override": state.pending_override,
        "override_mode": state.override_mode,
        "history": state.history,
        "brain_engine": state.brain_engine,
        "skills_file": state.skills_file,
        "has_api_key": auth_mode == "api_key",
        "has_vertex_auth": auth_mode == "vertex_ai",
        "has_auth": is_auth,
        "auth_valid": is_auth,
        "auth_health": auth_health,
        "auth_mode": auth_mode,
        "auth_status": auth_health.get("status", "unknown"),
        "auth_error": auth_err,
        "reauth_command": auth_health.get("reauth_command") or "gcloud auth application-default login",
        "gcp_project": os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        "last_engine_source": state.last_engine_source,
        "last_engine_error": state.last_engine_error,
        "last_fallback_reason": state.last_fallback_reason,
        "is_deliberating": state.is_deliberating,
        "active_directive": state.active_directive,
        "history_window_size": state.history_window_size,
        "thinking": thinking_budget_status(),
        # The workbench polls this endpoint and only this endpoint, so Box 6 is
        # rendered from here. Built by the shared helper rather than restated:
        # the previous inline copy was missing the coach's `thinking` block,
        # which left the slider with no bounds to move between.
        "evaluator": evaluator_status(),
        "chat_history": state.chat_history[-50:],
    }


@app.post("/api/agent/auth/verify")
async def verify_auth_endpoint() -> Dict[str, Any]:
    """Force-checks authentication status and clears errors if reauthenticated."""
    health = check_gemini_auth_health(force_refresh=True)
    if health.get("authenticated"):
        # The credential problem is genuinely resolved, so the error goes. But we
        # do NOT touch `last_engine_source`: no Gemini decision has been made yet.
        # It will correct itself on the next tick that actually reaches the model.
        state.last_engine_error = None
    else:
        state.last_engine_error = health.get("error")
        state.last_engine_source = "heuristic_fallback"
        state.last_fallback_reason = health.get("error")
    return health


@app.get("/api/agent/server/status")
def get_server_status() -> Dict[str, Any]:
    return ws_client.get_status()


@app.get("/api/agent/server/cats")
async def list_server_cats() -> Dict[str, Any]:
    server_http = ws_client.server_url.replace("wss://", "https://").replace("ws://", "http://").split("/ws/")[0]
    cats = []
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{server_http}/api/simulations/active/agents")
            if resp.status_code == 200:
                agents = resp.json()
                cats = [a for a in agents if a.get("type") == "cat"]
    except Exception as exc:
        return {"cats": [], "error": str(exc), "current": ws_client.assigned_id or state.agent_id}
    return {"cats": cats, "current": ws_client.assigned_id or state.agent_id}


@app.post("/api/agent/server/connect")
async def connect_to_server(req: Optional[ConnectServerRequest] = None) -> Dict[str, Any]:
    url = req.server_url if req and req.server_url else None
    cat_id = req.cat_id if req and req.cat_id else None
    await ws_client.connect(url, cat_id=cat_id)
    return ws_client.get_status()


@app.post("/api/agent/server/disconnect")
async def disconnect_from_server() -> Dict[str, Any]:
    await ws_client.disconnect()
    return ws_client.get_status()


@app.post("/api/agent/set-key")
@app.post("/api/agent/config/key")
def set_api_key(req: ApiKeyRequest) -> Dict[str, Any]:
    key = req.api_key.strip()
    if key:
        os.environ["GEMINI_API_KEY"] = key
        # When explicit API key is saved, disable vertex flag so it uses Gemini API directly
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
        health = check_gemini_auth_health(force_refresh=True)
        # Clear the credential error only. `last_engine_source` describes the last
        # decision that actually happened and is not ours to rewrite here.
        state.last_engine_error = None
        return {"status": "ok", "has_api_key": True, "auth_health": health}
    else:
        os.environ.pop("GEMINI_API_KEY", None)
        health = check_gemini_auth_health(force_refresh=True)
        if not health.get("authenticated"):
            state.last_engine_source = "heuristic_fallback"
            state.last_engine_error = health.get("error")
        return {"status": "cleared", "has_api_key": False, "auth_health": health}


@app.get("/api/agent/skills")
def get_skills() -> Dict[str, str]:
    return {"content": get_skills_instruction(), "path": str(SKILLS_PATH)}


@app.post("/api/agent/override")
def set_agent_override(req: OverrideRequest) -> Dict[str, Any]:
    if req.action:
        clean_action = req.action.strip().lower()
        state.pending_override = clean_action
        if req.mode in ["once", "sticky"]:
            state.override_mode = req.mode
    else:
        state.pending_override = None

    return {
        "status": "ok",
        "pending_override": state.pending_override,
        "override_mode": state.override_mode,
    }


@app.post("/api/agent/history-window")
def set_history_window(req: HistoryWindowRequest) -> Dict[str, Any]:
    state.history_window_size = max(1, min(50, req.history_window_size))
    return {
        "status": "ok",
        "history_window_size": state.history_window_size,
    }


def thinking_budget_status() -> Dict[str, Any]:
    """Everything the UI needs to render the control honestly.

    The bounds travel with the value rather than being duplicated in
    JavaScript. They depend on which model the actor runs, and a front end
    holding its own copy of "0 to 24,576" would keep offering that range after
    the model changed underneath it -- the slider would look fine and every
    tick would fail.
    """
    budget = get_thinking_budget()
    timeout_sec = get_deliberation_timeout()
    return {
        "budget": budget,
        "label": describe_thinking_budget(budget),
        "model": ACTOR_MODEL,
        "off": THINKING_BUDGET_OFF,
        "auto": THINKING_BUDGET_AUTO,
        "min": THINKING_BUDGET_MIN,
        "max": THINKING_BUDGET_MAX,
        "can_disable": THINKING_CAN_BE_DISABLED,
        # Latency ceiling a decision has to land inside. Reported alongside the
        # budget because it is what turns an ambitious budget into a heuristic
        # tick rather than a slow one.
        "deliberation_timeout_sec": timeout_sec,
        "deliberation_timeout_min_sec": DELIBERATION_TIMEOUT_MIN_SEC,
        "deliberation_timeout_max_sec": DELIBERATION_TIMEOUT_MAX_SEC,
        # Roughly how much thinking the current ceiling pays for. An estimate,
        # and labelled as one in the UI.
        "affordable_tokens": affordable_thinking_tokens(timeout_sec),
        "suggested_timeout_sec": suggested_deliberation_timeout(budget),
        "warning": thinking_budget_warning(budget),
        "changes": list(state.thinking_budget_changes),
    }


@app.get("/api/agent/thinking-budget")
def get_thinking_budget_endpoint() -> Dict[str, Any]:
    """The actor's current scratchpad allowance, read off the live agent."""
    return {"status": "ok", **thinking_budget_status()}


@app.post("/api/agent/thinking-budget")
def set_thinking_budget_endpoint(req: ThinkingBudgetRequest, response: Response) -> Dict[str, Any]:
    """Changes how much the actor may think before answering. Applies next tick.

    Scoped to the actor alone. The radio agent and the evaluator keep their own
    settings, so an experiment on the thing that plays the game does not quietly
    become an experiment on all three.

    Both values are validated before either is applied. A request that sets a
    good budget and a bad timeout changes nothing at all, rather than leaving
    the actor in a state no one asked for and no one is looking at.

    A rejected value returns 400. The alternative -- clamping to the nearest
    legal value -- would let the archive record a budget nobody chose, and this
    endpoint exists to make the archive trustworthy.
    """
    previous_budget = get_thinking_budget()
    previous_timeout = get_deliberation_timeout()

    try:
        budget = coerce_thinking_budget(req.thinking_budget)
        timeout_sec = (
            coerce_deliberation_timeout(req.deliberation_timeout_sec)
            if req.deliberation_timeout_sec is not None
            else previous_timeout
        )
    except ValueError as exc:
        response.status_code = 400
        return {
            "status": "error",
            "detail": str(exc),
            # The current settings travel with the rejection so the UI can snap
            # the control back to what is actually in force rather than leaving
            # it showing a value the agent refused.
            **thinking_budget_status(),
        }

    set_thinking_budget(budget)
    set_deliberation_timeout(timeout_sec)

    if budget != previous_budget or timeout_sec != previous_timeout:
        state.thinking_budget_changes.append({
            "at_tick": state.active_tick,
            "at": datetime.now(timezone.utc).isoformat(),
            "from": previous_budget,
            "to": budget,
            "timeout_from_sec": previous_timeout,
            "timeout_to_sec": timeout_sec,
        })
        # Re-published rather than left until restart: the archive is copied
        # when the *game* ends, which is usually long before the process does.
        publish_run_config()
        logger.info(
            "Thinking budget changed at tick %s: %s -> %s (timeout %gs -> %gs)",
            state.active_tick,
            describe_thinking_budget(previous_budget),
            describe_thinking_budget(budget),
            previous_timeout,
            timeout_sec,
        )

    return {"status": "ok", **thinking_budget_status()}



@app.post("/api/agent/clear-history")
def clear_history() -> Dict[str, str]:
    state.history.clear()
    # A manual history wipe is a deliberate fresh start, so the durable transcript
    # and any standing orders derived from it go too.
    reset_reflexion_state(state.active_tick)
    return {"status": "ok"}


def eval_thinking_status() -> Dict[str, Any]:
    """Everything the UI needs to render the evaluator's dial honestly.

    Shaped identically to `thinking_budget_status()` so the front end can drive
    both controls through one renderer. The two differ only in which timeout
    they report, and that difference is carried in the values rather than the
    key names for exactly that reason.
    """
    budget = get_eval_thinking_budget()
    timeout_sec = get_eval_timeout()
    return {
        "budget": budget,
        "label": describe_thinking_budget(budget),
        "model": EVAL_MODEL,
        "off": THINKING_BUDGET_OFF,
        "auto": THINKING_BUDGET_AUTO,
        "min": EVAL_THINKING_MIN,
        "max": EVAL_THINKING_MAX,
        "can_disable": EVAL_THINKING_CAN_BE_DISABLED,
        "timeout_sec": timeout_sec,
        "timeout_min_sec": EVAL_TIMEOUT_MIN_SEC,
        "timeout_max_sec": EVAL_TIMEOUT_MAX_SEC,
        "affordable_tokens": eval_affordable_thinking_tokens(timeout_sec),
        "suggested_timeout_sec": suggested_eval_timeout(budget),
        "warning": eval_thinking_warning(budget),
        "changes": list(state.eval_thinking_budget_changes),
        # What the last evaluation actually spent. A budget is a ceiling, not a
        # spend, and on a once-per-100-ticks agent there is no stream of
        # per-tick records to infer it from -- so it is reported here or not at
        # all.
        "last_run": get_last_eval_telemetry(),
    }


def evaluator_status() -> Dict[str, Any]:
    """Everything Box 6 renders, built in exactly one place.

    This used to be written out twice -- once inline in `/api/agent/state` and
    once in `/api/agent/evaluator` -- on the assumption that two short dict
    literals were cheaper than an indirection. They then drifted, and the
    failure was not obvious: the coach's thinking block was added to the
    evaluator endpoint only, while the workbench polls `/api/agent/state`. The
    control therefore never received its bounds, silently kept the placeholder
    ladder it was born with, and posted an undefined budget the moment anyone
    touched the slider.

    Nothing polls `/api/agent/evaluator` today; it is kept because it is a
    documented endpoint and convenient from curl. That is precisely the
    condition under which a duplicated payload rots unnoticed, so the duplicate
    is gone rather than merely corrected.
    """
    interval = max(1, state.eval_interval)
    return {
        "enabled": state.eval_enabled,
        "interval": interval,
        "is_evaluating": state.is_evaluating,
        "last_dispatch_tick": state.last_eval_dispatch_tick,
        "next_dispatch_tick": state.last_eval_dispatch_tick + interval,
        "transcript_ticks": store.transcript_tick_count(),
        "coaching_notes": store.read_coaching_notes(),
        "last_result": state.last_eval_result,
        # Carried on the poll the workbench already makes, so the control stays
        # in sync with a change made from another tab or from curl.
        "thinking": eval_thinking_status(),
    }


@app.get("/api/agent/evaluator")
def get_evaluator_status() -> Dict[str, Any]:
    """Current state of the long-horizon reflexion loop."""
    return evaluator_status()


@app.get("/api/agent/evaluator/thinking-budget")
def get_eval_thinking_budget_endpoint() -> Dict[str, Any]:
    """The evaluator's current scratchpad allowance, read off the live agent."""
    return {"status": "ok", **eval_thinking_status()}


@app.post("/api/agent/evaluator/thinking-budget")
def set_eval_thinking_budget_endpoint(
    req: EvalThinkingBudgetRequest, response: Response
) -> Dict[str, Any]:
    """Changes how much the evaluator may think. Applies to the next evaluation.

    Scoped to the evaluator alone, and deliberately a separate endpoint from the
    actor's. Raising both at once is a perfectly reasonable thing to want, but
    it has to be two explicit calls: a single control that moved both would make
    every run afterwards a two-variable experiment, and the reason this dial
    exists at all is to keep them apart.

    Both values are validated before either is applied, so a request carrying a
    good budget and a bad timeout changes nothing rather than leaving the
    evaluator in a state no one asked for.
    """
    previous_budget = get_eval_thinking_budget()
    previous_timeout = get_eval_timeout()

    try:
        budget = coerce_eval_thinking_budget(req.thinking_budget)
        timeout_sec = (
            coerce_eval_timeout(req.eval_timeout_sec)
            if req.eval_timeout_sec is not None
            else previous_timeout
        )
    except ValueError as exc:
        response.status_code = 400
        return {
            "status": "error",
            "detail": str(exc),
            # The live settings ride along with the rejection so the UI can snap
            # back to what is actually in force.
            **eval_thinking_status(),
        }

    set_eval_thinking_budget(budget)
    set_eval_timeout(timeout_sec)

    if budget != previous_budget or timeout_sec != previous_timeout:
        state.eval_thinking_budget_changes.append({
            "at_tick": state.active_tick,
            "at": datetime.now(timezone.utc).isoformat(),
            "from": previous_budget,
            "to": budget,
            "timeout_from_sec": previous_timeout,
            "timeout_to_sec": timeout_sec,
        })
        publish_run_config()
        logger.info(
            "Evaluator thinking budget changed at tick %s: %s -> %s (timeout %gs -> %gs)",
            state.active_tick,
            describe_thinking_budget(previous_budget),
            describe_thinking_budget(budget),
            previous_timeout,
            timeout_sec,
        )

    return {"status": "ok", **eval_thinking_status()}


@app.post("/api/agent/evaluator/interval")
def set_evaluator_interval(req: EvalIntervalRequest) -> Dict[str, Any]:
    """Adjusts how many ticks accumulate before an evaluation fires."""
    state.eval_interval = max(1, min(10000, req.interval))
    if req.enabled is not None:
        state.eval_enabled = req.enabled
    return {
        "status": "ok",
        "interval": state.eval_interval,
        "enabled": state.eval_enabled,
    }


@app.get("/api/agent/prompt/{tick}")
def get_prompt_for_tick(tick: int) -> Dict[str, Any]:
    """Returns the full model input behind the decision applied at `tick`.

    A miss is not an error. Most ticks are decided by the System-1 reflex, which
    never calls Gemini and therefore has no prompt to show. The response
    distinguishes that expected case from an empty log so the UI can say
    something truthful instead of just failing.
    """
    record = store.read_prompt_for_tick(tick)
    if record:
        return {"status": "ok", "tick": tick, "record": record}

    size = store.prompt_log_size()
    if size["records"] == 0:
        detail = (
            "No prompts have been captured yet. The log is wiped on game reset, "
            "and only ticks decided by Gemini produce a record."
        )
    else:
        detail = (
            f"Tick {tick} has no captured prompt. It was almost certainly decided "
            f"by the System-1 reflex heuristic, which never calls Gemini."
        )
    return {"status": "not_found", "tick": tick, "record": None, "detail": detail}


@app.get("/api/agent/prompt-log/stats")
def get_prompt_log_stats() -> Dict[str, Any]:
    """Size of the prompt log, so the UI can show growth without downloading it."""
    return {"status": "ok", **store.prompt_log_size(), "path": str(store.PROMPTS_PATH)}


@app.get("/api/agent/directive")
def get_commander_directive() -> Dict[str, Any]:
    """The commander's standing radio order, if one is in force.

    Reported separately from `active_directive`, which holds the model's own
    strategy. Conflating the two is what let the model erase the commander.
    """
    directive = state.commander_directive
    if not directive:
        return {"status": "ok", "directive": None, "age_ticks": None}
    issued = directive.get("tick")
    age = (state.active_tick - issued) if isinstance(issued, int) else None
    return {"status": "ok", "directive": directive, "age_ticks": age}


@app.delete("/api/agent/directive")
def clear_commander_directive() -> Dict[str, Any]:
    """Rescinds the standing radio order.

    Orders stand until replaced or cancelled, so without this an order issued
    once would steer the cat for the remainder of the game.
    """
    had = state.commander_directive is not None
    state.commander_directive = None
    return {"status": "ok", "cleared": had}


@app.get("/api/agent/evaluator/skills")
def get_evaluator_skills() -> Dict[str, str]:
    """The evaluator's instruction manual, distinct from the actor's."""
    return {"content": get_eval_instruction(), "path": str(EVAL_SKILLS_PATH)}


@app.post("/api/agent/evaluator/run")
async def run_evaluator_now() -> Dict[str, Any]:
    """Forces an evaluation immediately, ignoring the tick boundary."""
    if state.is_evaluating:
        return {"status": "busy", "detail": "An evaluation is already running."}
    state.eval_pending = False
    await _run_evaluation_cycle()
    return {"status": "ok", "result": state.last_eval_result}


@app.post("/api/agent/prompt", response_model=InterrogateResponse)
async def interrogate_agent(req: InterrogateRequest) -> InterrogateResponse:
    """Interrogate or prompt the cat agent directly via human plain text."""
    clean_prompt = req.prompt.strip()
    if not clean_prompt:
        return InterrogateResponse(
            reply="Commander, I received an empty transmission.",
            engine="heuristic_fallback",
            tick=state.active_tick,
            posture=state.latest_observation.get("self", {}) if state.latest_observation else {},
            active_strategy=(state.active_directive or {}).get("strategy", "ROAMING"),
        )

    obs = None
    if state.latest_observation:
        try:
            obs = Observation(**state.latest_observation)
        except Exception:
            obs = None

    reply, engine, err = await interrogate_adk_gemini(
        user_prompt=clean_prompt,
        obs=obs,
        history=state.history,
        agent_id=state.agent_id,
        history_window_size=state.history_window_size,
        # A standing radio order outranks the model's own strategy, so the chat
        # reply should answer in light of it rather than contradicting it.
        active_directive=state.commander_directive or state.active_directive,
    )

    adopted = None
    if req.steer_directive:
        # Its OWN slot, not `active_directive`. The model's background
        # deliberation overwrites `active_directive` wholesale, which is exactly
        # how commander orders used to get erased within a tick or two.
        state.commander_directive = {
            "directive": clean_prompt,
            "tick": state.active_tick,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "source": "commander_radio",
        }
        adopted = clean_prompt

    now_str = datetime.now().strftime("%H:%M:%S")
    state.chat_history.append({
        "id": f"msg-{len(state.chat_history)+1}-user",
        "sender": "commander",
        "message": clean_prompt,
        "timestamp": now_str,
        "tick": state.active_tick,
        "source": "user",
    })
    state.chat_history.append({
        "id": f"msg-{len(state.chat_history)+1}-agent",
        "sender": "cat",
        "message": reply,
        "timestamp": now_str,
        "tick": state.active_tick,
        "source": engine,
        "error": err,
        "adopted_directive": adopted,
    })

    posture = state.latest_observation.get("self", {}) if state.latest_observation else {}
    strat = (state.active_directive or {}).get("strategy", "ROAMING")

    return InterrogateResponse(
        reply=reply,
        engine=engine,
        tick=state.active_tick,
        posture=posture,
        active_strategy=strat,
        adopted_directive=adopted,
        error=err,
    )


@app.get("/api/agent/chat-history")
def get_chat_history() -> List[Dict[str, Any]]:
    return state.chat_history


@app.post("/api/agent/clear-chat")
def clear_chat_history() -> Dict[str, str]:
    state.chat_history.clear()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# General Radio: the Commander's channel to the coach.
#
# Mirrors the Commander Radio above endpoint for endpoint, but every route is
# namespaced under /general and every slot it touches is separate. The only
# thing the two channels share is the shape of the request.
# ---------------------------------------------------------------------------


@app.post("/api/agent/general/prompt", response_model=GeneralInterrogateResponse)
async def interrogate_general_agent(
    req: GeneralInterrogateRequest,
) -> GeneralInterrogateResponse:
    """Put a question to the coach, and optionally leave it under a standing order."""
    clean_prompt = req.prompt.strip()
    notes = store.read_coaching_notes()
    notes_count = sum(
        1 for ln in (notes or "").splitlines() if ln.strip().startswith("-")
    )

    if not clean_prompt:
        return GeneralInterrogateResponse(
            reply="Commander, I received an empty transmission.",
            engine="offline_fallback",
            tick=state.active_tick,
            notes_in_force=notes_count,
        )

    existing = get_coach_directive() or {}

    # The coach reasons over the transcript it is actually fed, so the radio
    # hands it the same window the next evaluation would see rather than the
    # whole game. A question about "what you just told the cat" should be
    # answerable from the evidence that produced those notes -- and the file is
    # tens of megabytes by mid-game, so the slice is not optional.
    try:
        all_records = store.read_transcript_records()
        window = max(1, int(state.eval_interval or 1))
        records = all_records[-window:] if all_records else None
    except Exception:
        # A radio question must still get an answer if the transcript is
        # unreadable; the coach simply answers from its dossier alone.
        records = None

    reply, engine, err = await interrogate_general(
        user_prompt=clean_prompt,
        notes=notes,
        records=records,
        standing_order=existing.get("directive"),
        tick=state.active_tick,
    )

    adopted = None
    if req.steer_directive:
        # Recorded whatever the reply said, including on the fallback path. The
        # order is the Commander's, not the coach's, and it must not be lost
        # just because the officer happened to be off the air when it was given.
        set_coach_directive(clean_prompt, tick=state.active_tick)
        adopted = clean_prompt

    now_str = datetime.now().strftime("%H:%M:%S")
    # The human operator on this channel is the General; the officer answering
    # is the Coach. One level down, on the Cat's channel, the same human speaks
    # as the Commander and the Cat answers. The rank matches the tier of the
    # agent being held to account, so the two logs never read alike.
    state.general_chat_history.append({
        "id": f"gen-{len(state.general_chat_history)+1}-user",
        "sender": "general",
        "message": clean_prompt,
        "timestamp": now_str,
        "tick": state.active_tick,
        "source": "user",
    })
    state.general_chat_history.append({
        "id": f"gen-{len(state.general_chat_history)+1}-agent",
        "sender": "coach",
        "message": reply,
        "timestamp": now_str,
        "tick": state.active_tick,
        "source": engine,
        "error": err,
        "adopted_directive": adopted,
    })

    return GeneralInterrogateResponse(
        reply=reply,
        engine=engine,
        tick=state.active_tick,
        adopted_directive=adopted,
        notes_in_force=notes_count,
        error=err,
    )


@app.get("/api/agent/general/chat-history")
def get_general_chat_history() -> List[Dict[str, Any]]:
    return state.general_chat_history


@app.post("/api/agent/general/clear-chat")
def clear_general_chat_history() -> Dict[str, str]:
    """Clears the radio log only.

    Deliberately does NOT rescind the standing order. Tidying up a conversation
    and revoking an order are different intentions, and silently coupling them
    would mean the Commander lost a constraint they never asked to drop.
    """
    state.general_chat_history.clear()
    return {"status": "ok"}


@app.get("/api/agent/general/directive")
def get_general_directive() -> Dict[str, Any]:
    """The Commander's standing order to the coach, if one is in force."""
    directive = get_coach_directive()
    return {
        "active": directive is not None,
        "directive": directive,
        "skills_path": str(GENERAL_RADIO_SKILLS_PATH),
        "timeout_sec": GENERAL_RADIO_TIMEOUT_SECONDS,
    }


@app.post("/api/agent/general/clear-directive")
def clear_general_directive() -> Dict[str, Any]:
    """Rescinds the standing order. The next evaluation runs without it."""
    had = clear_coach_directive()
    return {"status": "ok", "cleared": had}



# Mount static assets and serve workbench dashboard
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def get_workbench():
    return FileResponse(static_dir / "index.html")


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🐱</text></svg>"""


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    print(f"\n=======================================================", flush=True)
    print(f"🐱 Cat Agent Service & Interactive Workbench Online", flush=True)
    print(f"   • Brain: Google ADK (gemini-2.5-flash) with Skills Handbook", flush=True)
    print(f"   • Workbench Dashboard: http://127.0.0.1:{port}", flush=True)
    print(f"   • API Observation Endpoint: http://127.0.0.1:{port}/act", flush=True)
    print(f"=======================================================\n", flush=True)
    uvicorn.run("actors.cat.api:app", host="127.0.0.1", port=port, reload=True, log_level="warning")
