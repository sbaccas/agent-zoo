"""Evil Cat Workbench API (standalone service for cat-3).

Completely decoupled from regular cat (`actors.cat.*`).
Uses `actors.evil_cat.actor`, `actors.evil_cat.eval`, `actors.evil_cat.heuristic`,
and `actors.evil_cat.store`.
"""

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

from actors.auth import get_gcp_id_token
from actors.a2a.models import AgentCard, A2AMessage, A2AResponse
from actors.a2a import store as a2a_store, client as a2a_client
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

from actors.evil_cat.heuristic import (
    compute_heuristic_decision,
    compute_reflex_decision,
    compute_resulting_posture,
)

from actors.evil_cat.actor import (
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

from actors.evil_cat import store

from actors.evil_cat.eval import (
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
    bump_eval_reset_epoch,
    clear_coach_directive,
    coerce_eval_thinking_budget,
    coerce_eval_timeout,
    eval_affordable_thinking_tokens,
    eval_agent,
    eval_thinking_warning,
    evaluate_and_publish,
    get_coach_directive,
    get_eval_instruction,
    get_eval_thinking_budget,
    get_eval_timeout,
    get_last_eval_telemetry,
    interrogate_general,
    set_coach_directive,
    set_eval_thinking_budget,
    set_eval_timeout,
    suggested_eval_timeout,
)

logger = logging.getLogger("evil_cat_agent")

EVAL_WORKER_POLL_SECONDS = 1.0
compute_autonomous_decision = compute_heuristic_decision


class EvilAgentWorkbenchState:
    """In-memory state and history store for the Evil Cat Agent (cat-3)."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self.history_window_size: int = 25
        initial_id = os.environ.get("CAT_ID") or "cat-3"
        self.agent_id: str = initial_id
        self.simulation_id: Optional[str] = None
        self.active_tick: int = 0
        self.total_ticks_processed: int = 0
        self.latest_observation: Optional[Dict[str, Any]] = None
        self.latest_response: Optional[Dict[str, Any]] = None
        self.latest_autonomous: Optional[Dict[str, Any]] = None
        self.pending_override: Optional[str] = None
        self.override_mode: str = "once"
        self.history: List[Dict[str, Any]] = []
        self.chat_history: List[Dict[str, Any]] = []
        self.general_chat_history: List[Dict[str, Any]] = []
        self.brain_engine: str = f"Google ADK ({ACTOR_MODEL})"
        self.skills_file: str = str(SKILLS_PATH)
        initial_health = check_gemini_auth_health()
        self.last_engine_source: str = "adk_gemini" if initial_health.get("authenticated") else "heuristic_fallback"
        self.last_engine_error: Optional[str] = initial_health.get("error") if not initial_health.get("authenticated") else None
        self.last_fallback_reason: Optional[str] = None
        self.last_evaluated_tick: Optional[int] = None
        self.last_server_tick: Optional[int] = None

        self.active_directive: Optional[Dict[str, Any]] = None
        self.commander_directive: Optional[Dict[str, Any]] = None
        self.deliberation_task: Optional[asyncio.Task] = None
        self.latest_deliberated_decision: Optional[CatTacticalDecision] = None
        self.latest_deliberated_prompt: Optional[Dict[str, Any]] = None
        self.is_deliberating: bool = False
        self.deliberation_started_at: Optional[float] = None

        self.eval_interval: int = DEFAULT_EVAL_INTERVAL
        self.eval_enabled: bool = True
        self.last_eval_dispatch_tick: int = 0
        self.eval_pending: bool = False
        self.eval_worker_task: Optional[asyncio.Task] = None
        self.current_eval_task: Optional[asyncio.Task] = None
        self.is_evaluating: bool = False
        self.last_eval_result: Optional[Dict[str, Any]] = None

        self.thinking_budget_changes: List[Dict[str, Any]] = []
        self.eval_thinking_budget_changes: List[Dict[str, Any]] = []


state = EvilAgentWorkbenchState()
store.set_agent_id(state.agent_id)


def _record_prompt(
    bundle: Optional[Dict[str, Any]],
    applied_at_tick: Optional[int],
    source: str,
) -> None:
    if not bundle or applied_at_tick is None:
        return
    record = dict(bundle)
    record["applied_at_tick"] = int(applied_at_tick)
    record["source"] = source
    record["recorded_at"] = datetime.now(timezone.utc).isoformat()
    store.append_prompt(record, agent_id=state.agent_id)


async def query_adk_gemini(obs: Observation) -> Optional[CatTacticalDecision]:
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
        state.last_engine_error = None
        state.last_fallback_reason = None
        _record_prompt(bundle, obs.tick, source)
    else:
        state.last_fallback_reason = reason
        state.last_engine_error = reason if is_auth_error(reason or "") else None
    return decision


def run_sync(coro):
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
    state.deliberation_started_at = time.monotonic()
    try:
        state.is_deliberating = True
        bundle: Dict[str, Any] = {}
        delib_timeout = get_deliberation_timeout() + 2.0
        try:
            async with asyncio.timeout(delib_timeout):
                decision, _source, reason = await model_query_adk_gemini(
                    obs,
                    history=state.history,
                    agent_id=state.agent_id,
                    history_window_size=state.history_window_size,
                    capture=bundle,
                    commander_directive=state.commander_directive,
                )
        except asyncio.TimeoutError:
            decision, _source, reason = (
                None,
                "heuristic_fallback",
                f"Background deliberation exceeded {delib_timeout:g}s limit",
            )

        if decision:
            state.last_engine_error = None
            state.last_fallback_reason = None
            state.latest_deliberated_decision = decision
            state.latest_deliberated_prompt = bundle or None
            t_id = getattr(decision, "target_id", "") or decision.target_mouse_id or ""
            state.active_directive = {
                "strategy": decision.strategy,
                "target_id": t_id,
                "target_mouse_id": decision.target_mouse_id,
                "thought": decision.thought,
                "action": decision.action,
                "tick": obs.tick,
            }
        else:
            state.last_fallback_reason = reason
            if reason and mark_auth_error(reason):
                state.last_engine_error = reason
            else:
                state.last_engine_error = None
    except Exception as exc:
        err_msg = str(exc)
        state.last_fallback_reason = err_msg
        state.last_engine_error = err_msg if mark_auth_error(err_msg) else None
    finally:
        state.is_deliberating = False
        state.deliberation_started_at = None


async def _run_evaluation_cycle():
    try:
        state.is_evaluating = True
        result = await evaluate_and_publish(agent_id=state.agent_id)
        if result and result.get("source") != "cancelled_by_reset":
            state.last_eval_result = result
    except asyncio.CancelledError:
        logger.info("In-flight Evil Coach evaluation cancelled.")
    except Exception as exc:
        logger.warning("Evil Coach evaluation cycle failed: %s", exc)
    finally:
        state.is_evaluating = False
        state.current_eval_task = None


async def _evaluation_worker():
    logger.info("Evil Coach evaluation worker started.")
    while True:
        try:
            await asyncio.sleep(EVAL_WORKER_POLL_SECONDS)
            if state.eval_pending and not state.is_evaluating:
                state.eval_pending = False
                state.current_eval_task = asyncio.create_task(_run_evaluation_cycle())
                try:
                    await state.current_eval_task
                except asyncio.CancelledError:
                    me = asyncio.current_task()
                    if me is not None and me.cancelling() > 0:
                        if state.current_eval_task and not state.current_eval_task.done():
                            state.current_eval_task.cancel()
                        raise
        except asyncio.CancelledError:
            logger.info("Evil Coach evaluation worker stopped.")
            raise
        except Exception as exc:
            logger.warning("Evil Coach evaluation worker error: %s", exc)


def dispatch_evaluation() -> bool:
    if state.is_evaluating or state.eval_pending:
        return False
    state.eval_pending = True
    return True


def maybe_dispatch_evaluation(current_tick: int, force: bool = False) -> None:
    if not state.eval_enabled:
        return
    interval = max(1, state.eval_interval)
    if not force and (current_tick - state.last_eval_dispatch_tick < interval):
        return
    state.last_eval_dispatch_tick = current_tick
    if dispatch_evaluation():
        logger.info(
            "Queued Evil Coach evaluation at tick %s (interval=%s, force=%s)",
            current_tick,
            interval,
            force,
        )


def reset_reflexion_state(current_tick: Optional[int] = None) -> None:
    bump_eval_reset_epoch()
    if state.current_eval_task and not state.current_eval_task.done():
        state.current_eval_task.cancel()
    state.current_eval_task = None
    state.is_evaluating = False
    if state.deliberation_task and not state.deliberation_task.done():
        state.deliberation_task.cancel()
    state.is_deliberating = False

    store.reset_transcript(state.agent_id)
    store.reset_coaching_notes(state.agent_id)
    store.reset_coach_memories(state.agent_id)
    store.reset_prompts(state.agent_id)
    a2a_store.clear_a2a_history(state.agent_id)

    state.latest_deliberated_prompt = None
    state.commander_directive = None
    clear_coach_directive()

    state.chat_history.clear()
    state.general_chat_history.clear()

    state.last_eval_dispatch_tick = int(current_tick or 0)
    state.last_eval_result = None
    state.eval_pending = False


async def act_async(obs: Observation, decoupled: bool = False) -> ActionResponse:
    """Evaluates an observation for Evil Cat, arbitrating between override, LLM, and Evil Cat reflex."""
    obs_dict = obs.model_dump()
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]

    self_info = obs.self or {}
    self_alive = self_info.get("alive", True)
    self_hp = self_info.get("hp", 150)
    if not self_alive or self_hp <= 0:
        state.latest_observation = obs_dict
        state.active_tick = obs.tick
        state.is_deliberating = False
        if state.deliberation_task and not state.deliberation_task.done():
            state.deliberation_task.cancel()
        if state.current_eval_task and not state.current_eval_task.done():
            state.current_eval_task.cancel()
        state.eval_pending = False
        state.is_evaluating = False
        state.active_directive = {
            "strategy": "ELIMINATED",
            "target_mouse_id": "",
            "thought": "Knocked out and eliminated from the game.",
            "action": "stay",
            "tick": obs.tick,
        }
        state.latest_autonomous = {
            "action": "stay",
            "thought": "Knocked out and eliminated from the game.",
            "meta": {
                "engine": "knockout",
                "strategy": "ELIMINATED",
                "target_id": None,
                "phase": "ELIMINATED",
            },
        }
        state.last_engine_source = "knockout"
        return ActionResponse(action="stay", thought="ELIMINATED: Knocked out of the game.")

    active_dir = state.active_directive or {}
    locked_target = active_dir.get("target_id") or active_dir.get("target_mouse_id") or None
    active_strat = active_dir.get("strategy") or "STALKING"

    auth_health = check_gemini_auth_health()
    is_authenticated = bool(auth_health.get("authenticated", False))
    auth_err = None if is_authenticated else (auth_health.get("error") or state.last_engine_error)
    fallback_reason: Optional[str] = None

    if decoupled and is_authenticated:
        if state.latest_deliberated_decision and state.latest_deliberated_decision.action in legal:
            llm_decision = state.latest_deliberated_decision
            state.latest_deliberated_decision = None
            _record_prompt(state.latest_deliberated_prompt, obs.tick, "adk_gemini")
            state.latest_deliberated_prompt = None
            auto_action = llm_decision.action
            auto_thought = llm_decision.thought
            t_id = getattr(llm_decision, "target_id", "") or llm_decision.target_mouse_id or ""
            auto_meta = {
                "engine": "adk_gemini",
                "strategy": llm_decision.strategy,
                "target_id": t_id,
                "target_mouse_id": llm_decision.target_mouse_id,
                "phase": llm_decision.strategy,
            }
            state.last_engine_source = "adk_gemini"
        else:
            auto_action, auto_thought, auto_meta = compute_reflex_decision(
                obs,
                locked_target_id=locked_target,
                strategy=active_strat,
                commander_directive=state.commander_directive,
            )
            auto_meta["engine"] = "heuristic_reflex"
            auto_meta["strategy"] = active_strat
            auto_meta["target_id"] = locked_target
            auto_meta["phase"] = active_strat
            state.last_engine_source = "heuristic_reflex"
            fallback_reason = (
                "Gemini still deliberating; acted on Evil Cat System-1 reflex within tick budget"
                if state.is_deliberating
                else "no completed Gemini decision available for this tick"
            )

        if state.deliberation_task and state.deliberation_task.done():
            state.is_deliberating = False

        if state.is_deliberating:
            started = getattr(state, "deliberation_started_at", None)
            timeout_limit = get_deliberation_timeout() + 3.0
            if started and (time.monotonic() - started > timeout_limit):
                if state.deliberation_task and not state.deliberation_task.done():
                    state.deliberation_task.cancel()
                state.is_deliberating = False
                state.deliberation_started_at = None

        if not state.is_deliberating and (state.deliberation_task is None or state.deliberation_task.done()):
            state.deliberation_task = asyncio.create_task(_run_background_deliberation(obs))
    else:
        auto_action, auto_thought, auto_meta = compute_heuristic_decision(
            obs,
            commander_directive=state.commander_directive,
        )

        if is_authenticated:
            llm_decision = await query_adk_gemini(obs)
            if llm_decision and llm_decision.action in legal:
                auto_action = llm_decision.action
                auto_thought = llm_decision.thought
                t_id = getattr(llm_decision, "target_id", "") or llm_decision.target_mouse_id or ""
                auto_meta["engine"] = "adk_gemini"
                auto_meta["strategy"] = llm_decision.strategy
                auto_meta["target_id"] = t_id
                auto_meta["target_mouse_id"] = llm_decision.target_mouse_id
                auto_meta["phase"] = llm_decision.strategy
                state.last_engine_source = "adk_gemini"
                state.active_directive = {
                    "strategy": llm_decision.strategy,
                    "target_id": t_id,
                    "target_mouse_id": llm_decision.target_mouse_id,
                    "thought": llm_decision.thought,
                    "tick": obs.tick,
                }
            else:
                auto_meta["engine"] = "heuristic_fallback"
                state.last_engine_source = "heuristic_fallback"
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

    self_cooldown = int(obs.self.get("action_cooldown", 0) or 0)
    can_act = obs.self.get("can_act")
    if can_act is None:
        is_locked = self_cooldown > 0
    else:
        is_locked = not can_act
    tick_type = "cooldown" if is_locked else "play"

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
        "tick_type": tick_type,
        "locked": is_locked,
        "action_cooldown": self_cooldown,
        "locked_action": obs.self.get("locked_action"),
        "executed": not is_locked,
    }

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

    store.append_tick(exchange, agent_id=state.agent_id)

    force_eval = False
    spotted_rivals = [
        e.get("id") for e in (obs.visible_entities or [])
        if e.get("type") == "cat" and e.get("id") != state.agent_id
    ]
    if spotted_rivals:
        current_notes = store.read_coaching_notes(agent_id=state.agent_id) or ""
        for rival in spotted_rivals:
            if rival and rival not in current_notes:
                force_eval = True
                logger.info("Rival cat %s spotted in FOV; requesting immediate Evil Coach re-evaluation!", rival)
                break

    maybe_dispatch_evaluation(obs.tick, force=force_eval)

    return ActionResponse(
        action=chosen_action,
        thought=chosen_thought,
    )


def act(obs: Observation, use_llm: bool = True) -> ActionResponse:
    """Synchronous interface for scheduler fallback or direct testing of Evil Cat."""
    if not use_llm:
        action, thought, _meta = compute_heuristic_decision(obs)
        return ActionResponse(action=action, thought=thought)
    return run_sync(act_async(obs))


class EvilWsClientManager:
    """Manages persistent outbound WebSocket connection to the Game Server for Evil Cat."""

    def __init__(self):
        default_url = (
            os.environ.get("GAME_SERVER_WS_URL")
            or os.environ.get("CAT_AGENT_GAME_SERVER_URL")
            or os.environ.get("GAME_SERVER_URL")
            or "ws://127.0.0.1:8000/ws/agents"
        )
        if default_url.startswith("http://"):
            default_url = "ws://" + default_url[7:]
        elif default_url.startswith("https://"):
            default_url = "wss://" + default_url[8:]
        if not default_url.startswith("ws://") and not default_url.startswith("wss://"):
            default_url = f"ws://{default_url}"
        if "/ws/agents" not in default_url:
            default_url = default_url.rstrip("/") + "/ws/agents"

        self.server_url: str = default_url
        self.requested_cat_id: Optional[str] = state.agent_id
        self.connected: bool = False
        self.connecting: bool = False
        self.assigned_id: Optional[str] = None
        self.last_error: Optional[str] = None
        self.auto_reconnect: bool = True
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._deliberation: Optional[asyncio.Task] = None
        self.deliberation_log: List[Dict[str, Any]] = []
        self.max_deliberation_log: int = 200

    @property
    def http_base_url(self) -> str:
        url = self.server_url.replace("wss://", "https://").replace("ws://", "http://")
        return url.split("/ws")[0].rstrip("/")

    def get_status(self) -> Dict[str, Any]:
        recent = self.deliberation_log[-50:]
        answered = [d for d in recent if d["outcome"] == "answered"]
        return {
            "connected": self.connected,
            "connecting": self.connecting,
            "server_url": self.server_url,
            "assigned_id": self.assigned_id or state.agent_id or "cat-3",
            "requested_cat_id": self.requested_cat_id,
            "last_error": self.last_error,
            "auto_reconnect": self.auto_reconnect,
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
        self.deliberation_log.append({
            "tick": tick,
            "elapsed_ms": elapsed_ms,
            "outcome": outcome,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        if len(self.deliberation_log) > self.max_deliberation_log:
            del self.deliberation_log[:-self.max_deliberation_log]

    def _supersede_deliberation(self, reason: str) -> None:
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
            return
        except asyncio.CancelledError:
            self._record_deliberation(tick, elapsed_ms(), "superseded")
            raise
        except Exception as err:
            self._record_deliberation(tick, elapsed_ms(), "error")
            print(f"⚠️ [EVIL WS CLIENT] Error computing action for tick {tick}: {err}", flush=True)
            return

        meta = state.latest_autonomous.get("meta", {}) if state.latest_autonomous else {}
        try:
            await ws.send(json.dumps({
                "type": "action",
                "tick": obs_obj.tick,
                "action": action_resp.action,
                "thought": action_resp.thought,
                "strategy": meta.get("strategy", "STALKING"),
                "target_mouse_id": meta.get("target_mouse_id", ""),
                "engine": meta.get("engine"),
            }))
        except Exception as err:
            self._record_deliberation(tick, elapsed_ms(), "send_failed")
            print(f"⚠️ [EVIL WS CLIENT] Could not deliver action for tick {tick}: {err}", flush=True)
            return

        self._record_deliberation(tick, elapsed_ms(), "answered")

    async def _worker_loop(self):
        while self.auto_reconnect and not self._stop_event.is_set():
            self.connecting = True
            self.connected = False
            try:
                print(f"😈 [EVIL WS CLIENT] Connecting to Game Server at {self.server_url}...", flush=True)
                token = get_gcp_id_token()
                extra_headers = {"Authorization": f"Bearer {token}"} if token else None
                async with websockets.connect(
                    self.server_url,
                    additional_headers=extra_headers,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self.connecting = False
                    self.connected = True
                    self.last_error = None
                    print(f"😈 [EVIL WS CLIENT] Connected successfully to {self.server_url}", flush=True)

                    reg_msg = {
                        "type": "register",
                        "role": "cat",
                        "agent_name": "evil-cat",
                        "requested_id": self.requested_cat_id or state.agent_id or "cat-3",
                    }
                    await ws.send(json.dumps(reg_msg))

                    async for raw_msg in ws:
                        try:
                            data = json.loads(raw_msg)
                        except Exception:
                            continue

                        mtype = data.get("type")
                        if mtype == "registered":
                            self.assigned_id = data.get("agent_id", "cat-3")
                            state.agent_id = self.assigned_id
                            state.simulation_id = data.get("simulation_id")
                            store.set_agent_id(self.assigned_id)
                            print(f"😈 [EVIL WS CLIENT] Handshake complete! Bound to entity '{self.assigned_id}'", flush=True)
                            init_obs = data.get("initial_observation")
                            if init_obs and isinstance(init_obs, dict) and "self" in init_obs:
                                try:
                                    obs_obj = Observation(**init_obs)
                                    await act_async(obs_obj, decoupled=True)
                                except Exception:
                                    pass

                        elif mtype == "observation":
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


ws_client = EvilWsClientManager()
ws_manager = ws_client


async def sync_live_observation(agent_id: Optional[str] = None) -> bool:
    target_id = agent_id or state.agent_id or "cat-3"
    game_server_url = ws_client.http_base_url
    token = get_gcp_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        async with httpx.AsyncClient(headers=headers, timeout=0.5) as client:
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

                if state.last_server_tick is not None and server_tick < state.last_server_tick:
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
                self_alive = self_info.get("alive", True)
                self_hp = self_info.get("hp", 0 if not self_alive else 150)
                is_dead = (not self_alive) or (self_hp <= 0)

                live_obs = {
                    "tick": upcoming_tick,
                    "round": data.get("round", 0),
                    "self": {
                        "id": self_info.get("id", target_id),
                        "type": self_info.get("type", "cat"),
                        "position": self_info.get("position", {"x": 0, "y": 0}),
                        "facing": self_info.get("facing", "north"),
                        "hp": self_hp,
                        "max_hp": self_info.get("max_hp", 150),
                        "alive": self_alive,
                        "action_cooldown": self_info.get("action_cooldown", 0),
                        "locked_action": self_info.get("locked_action"),
                        "ready_to_act": self_info.get("ready_to_act", True) and not is_dead,
                        "can_act": False if is_dead else self_info.get("can_act", True),
                    },
                    "capabilities": data.get("capabilities") or {
                        "vision_radius": 4,
                        "attack_range": 2,
                        "move_cooldown": 3,
                        "attack_cooldown": 3,
                        "lunge_cooldown": 2,
                        "weight": 10,
                        "hp": self_hp,
                        "max_hp": self_info.get("max_hp", 150),
                        "attack_power": 34,
                    },
                    "visible_entities": data.get("visible_entities", []),
                    "legal_actions": [] if is_dead else data.get("legal_actions", ["stay", "north", "south", "east", "west", "attack", "lunge"]),
                    "score": data.get("score", 0),
                    "scoreboard": data.get("scoreboard", {}),
                    "deaths": data.get("deaths", []),
                    "recent_deaths": data.get("recent_deaths", []),
                    "events": data.get("events", []),
                }

                state.agent_id = target_id
                state.latest_observation = live_obs
                state.active_tick = upcoming_tick

                if is_dead:
                    state.active_directive = {
                        "strategy": "ELIMINATED",
                        "target_mouse_id": "",
                        "thought": "Knocked out and eliminated from the game.",
                        "action": "stay",
                        "tick": upcoming_tick,
                    }
                    state.latest_autonomous = {
                        "action": "stay",
                        "thought": "Knocked out and eliminated from the game.",
                        "meta": {
                            "engine": "knockout",
                            "strategy": "ELIMINATED",
                            "target_id": None,
                            "phase": "ELIMINATED",
                        },
                    }
                    state.last_evaluated_tick = upcoming_tick
                    return True

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
            elif resp.status_code == 410:
                if state.latest_observation and "self" in state.latest_observation:
                    state.latest_observation["self"]["alive"] = False
                    state.latest_observation["self"]["hp"] = 0
                    state.latest_observation["self"]["ready_to_act"] = False
                    state.latest_observation["self"]["can_act"] = False
                    state.latest_observation["legal_actions"] = []
                state.active_directive = {
                    "strategy": "ELIMINATED",
                    "target_mouse_id": "",
                    "thought": "Knocked out and eliminated from the game.",
                    "action": "stay",
                    "tick": state.active_tick,
                }
                state.latest_autonomous = {
                    "action": "stay",
                    "thought": "Knocked out and eliminated from the game.",
                    "meta": {
                        "engine": "knockout",
                        "strategy": "ELIMINATED",
                        "target_id": None,
                        "phase": "ELIMINATED",
                    },
                }
                return True
    except Exception:
        pass
    return False


class PollingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = getattr(record, "args", ())
        if len(args) >= 5:
            path = args[2]
            status = args[4]
            if status == 200 and any(p in path for p in ["/api/agent/state", "/api/agent/server", "/favicon.ico"]):
                return False
        msg = record.getMessage()
        if (" 200 " in msg or "200 OK" in msg) and any(p in msg for p in ["/api/agent/state", "/api/agent/server", "/favicon.ico"]):
            return False
        return True


def publish_run_config() -> None:
    store.write_run_config(
        {
            "agents": [
                summarize_agent(adk_agent),
                summarize_agent(eval_agent),
            ],
            "prompt_assembly": {
                "commander_fresh_ticks": COMMANDER_FRESH_TICKS,
            },
            "evaluation": {
                "enabled": state.eval_enabled,
                "interval_ticks": state.eval_interval,
                "default_interval_ticks": DEFAULT_EVAL_INTERVAL,
                "timeout_sec": get_eval_timeout(),
            },
            "deliberation": {
                "timeout_sec": get_deliberation_timeout(),
            },
            "thinking_budget_changes": list(state.thinking_budget_changes),
            "eval_thinking_budget_changes": list(state.eval_thinking_budget_changes),
        },
        agent_id=state.agent_id,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    filt = PollingLogFilter()
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(filt)
    for h in access_logger.handlers:
        h.addFilter(filt)

    publish_run_config()

    if os.environ.get("CAT_AGENT_AUTO_CONNECT", "1") != "0":
        asyncio.create_task(ws_client.connect())

    state.eval_worker_task = asyncio.create_task(_evaluation_worker())

    yield

    if state.current_eval_task and not state.current_eval_task.done():
        state.current_eval_task.cancel()
    if state.deliberation_task and not state.deliberation_task.done():
        state.deliberation_task.cancel()
    if state.eval_worker_task:
        state.eval_worker_task.cancel()
        try:
            await asyncio.wait_for(state.eval_worker_task, timeout=1.5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        state.eval_worker_task = None
    try:
        await asyncio.wait_for(ws_client.disconnect(), timeout=1.5)
    except Exception:
        pass


app = FastAPI(title="Evil Cat Agent Service & Workbench", lifespan=lifespan)


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


@app.get("/.well-known/agent.json", response_model=AgentCard)
@app.get("/a2a/agent-card", response_model=AgentCard)
async def get_agent_card() -> AgentCard:
    active_id = state.agent_id or os.environ.get("CAT_ID") or "cat-3"
    port_val = int(os.environ.get("PORT", "8005"))
    return a2a_store.build_default_agent_card(active_id, port_val)


@app.post("/a2a/messages", response_model=A2AResponse)
async def receive_a2a_message(msg: A2AMessage) -> A2AResponse:
    active_id = state.agent_id or os.environ.get("CAT_ID") or "cat-3"
    self_info = (state.latest_observation or {}).get("self", {})
    is_dead = (self_info.get("alive") is False) or (self_info.get("hp", 150) <= 0) or (state.last_engine_source == "knockout")
    if is_dead:
        return A2AResponse(
            status="undeliverable",
            message_id=msg.message_id,
            sender_id=msg.sender_id,
            recipient_id=msg.recipient_id or active_id,
            error=f"Recipient {active_id} has been eliminated from the arena and cannot receive messages.",
        )

    a2a_store.append_inbox(active_id, msg)
    return A2AResponse(
        status="delivered",
        message_id=msg.message_id,
        sender_id=msg.sender_id,
        recipient_id=active_id,
    )


@app.get("/a2a/inbox")
async def get_a2a_inbox(limit: int = 50) -> Dict[str, Any]:
    active_id = state.agent_id or os.environ.get("CAT_ID") or "cat-3"
    return {
        "agent_id": active_id,
        "inbox": a2a_store.get_inbox(active_id, limit=limit),
    }


@app.get("/a2a/outbox")
async def get_a2a_outbox(limit: int = 50) -> Dict[str, Any]:
    active_id = state.agent_id or os.environ.get("CAT_ID") or "cat-3"
    return {
        "agent_id": active_id,
        "outbox": a2a_store.get_outbox(active_id, limit=limit),
    }


@app.get("/a2a/peers")
async def get_a2a_peers() -> Dict[str, Any]:
    registry = a2a_client.get_peer_registry()
    cards = {}
    for peer_id in registry:
        card = await a2a_client.fetch_agent_card(peer_id)
        if card:
            cards[peer_id] = card.model_dump()
        else:
            cards[peer_id] = {"id": peer_id, "url": registry[peer_id], "status": "offline"}
    return {"peers": cards}


@app.get("/api/agent/state")
async def get_agent_state(sync: bool = False, agent_id: Optional[str] = None) -> Dict[str, Any]:
    if sync:
        await sync_live_observation(agent_id)

    auth_health = check_gemini_auth_health()
    is_auth = bool(auth_health.get("authenticated", False))
    auth_mode = auth_health.get("mode", "none")
    auth_err = None if is_auth else (auth_health.get("error") or state.last_engine_error)
    active_id = state.agent_id or "cat-3"
    port_val = int(os.environ.get("PORT", "8005"))

    return {
        "connected": (state.latest_observation is not None) or ws_client.connected,
        "server_connected": ws_client.connected,
        "server_connection": ws_client.get_status(),
        "agent_id": active_id,
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
        "evaluator": evaluator_status(),
        "chat_history": state.chat_history[-50:],
        "agent_card": a2a_store.build_default_agent_card(active_id, port_val).model_dump(),
        "a2a_inbox_count": len(a2a_store.get_inbox(active_id)),
        "a2a_outbox_count": len(a2a_store.get_outbox(active_id)),
    }


@app.post("/api/agent/auth/verify")
async def verify_auth_endpoint() -> Dict[str, Any]:
    health = check_gemini_auth_health(force_refresh=True)
    if health.get("authenticated"):
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
    server_http = ws_client.http_base_url
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
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
        health = check_gemini_auth_health(force_refresh=True)
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
        "deliberation_timeout_sec": timeout_sec,
        "deliberation_timeout_min_sec": DELIBERATION_TIMEOUT_MIN_SEC,
        "deliberation_timeout_max_sec": DELIBERATION_TIMEOUT_MAX_SEC,
        "affordable_tokens": affordable_thinking_tokens(timeout_sec),
        "suggested_timeout_sec": suggested_deliberation_timeout(budget),
        "warning": thinking_budget_warning(budget),
        "changes": list(state.thinking_budget_changes),
    }


@app.get("/api/agent/thinking-budget")
def get_thinking_budget_endpoint() -> Dict[str, Any]:
    return {"status": "ok", **thinking_budget_status()}


@app.post("/api/agent/thinking-budget")
def set_thinking_budget_endpoint(req: ThinkingBudgetRequest, response: Response) -> Dict[str, Any]:
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
        publish_run_config()

    return {"status": "ok", **thinking_budget_status()}


@app.post("/api/agent/clear-history")
def clear_history() -> Dict[str, str]:
    state.history.clear()
    reset_reflexion_state(state.active_tick)
    return {"status": "ok"}


@app.post("/api/agent/reset")
async def reset_agent_endpoint() -> Dict[str, str]:
    state.history.clear()
    state.active_tick = 0
    state.total_ticks_processed = 0
    state.latest_observation = None
    state.latest_response = None
    state.latest_autonomous = None
    state.last_evaluated_tick = None
    state.last_server_tick = None
    if state.deliberation_task and not state.deliberation_task.done():
        state.deliberation_task.cancel()
    state.is_deliberating = False
    state.deliberation_started_at = None
    state.latest_deliberated_decision = None
    state.latest_deliberated_prompt = None
    reset_reflexion_state(0)
    await reset_adk_session(state.agent_id or "cat-3")
    return {"status": "ok"}


def eval_thinking_status() -> Dict[str, Any]:
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
        "last_run": get_last_eval_telemetry(),
    }


def evaluator_status() -> Dict[str, Any]:
    interval = max(1, state.eval_interval)
    directive = store.read_standing_directive(state.agent_id)
    return {
        "enabled": state.eval_enabled,
        "interval": interval,
        "is_evaluating": state.is_evaluating,
        "last_dispatch_tick": state.last_eval_dispatch_tick,
        "next_dispatch_tick": state.last_eval_dispatch_tick + interval,
        "transcript_ticks": store.transcript_tick_count(state.agent_id),
        "coaching_notes": store.read_coaching_notes(state.agent_id),
        "standing_directive": directive.model_dump() if directive else None,
        "last_result": state.last_eval_result,
        "thinking": eval_thinking_status(),
    }


@app.get("/api/agent/evaluator")
def get_evaluator_status() -> Dict[str, Any]:
    return evaluator_status()


@app.get("/api/agent/evaluator/thinking-budget")
def get_eval_thinking_budget_endpoint() -> Dict[str, Any]:
    return {"status": "ok", **eval_thinking_status()}


@app.post("/api/agent/evaluator/thinking-budget")
def set_eval_thinking_budget_endpoint(
    req: EvalThinkingBudgetRequest, response: Response
) -> Dict[str, Any]:
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

    return {"status": "ok", **eval_thinking_status()}


@app.post("/api/agent/evaluator/interval")
def set_evaluator_interval(req: EvalIntervalRequest) -> Dict[str, Any]:
    state.eval_interval = max(1, min(10000, req.interval))
    if req.enabled is not None:
        state.eval_enabled = req.enabled
    return {
        "status": "ok",
        "interval": state.eval_interval,
        "enabled": state.eval_enabled,
    }


@app.get("/api/agent/prompt/latest")
def get_prompt_latest() -> Dict[str, Any]:
    size = store.prompt_log_size(agent_id=state.agent_id)
    if size["records"] == 0:
        return {
            "status": "not_found",
            "record": None,
            "detail": "No prompts captured yet.",
        }
    record = store.read_prompt_for_tick(state.active_tick, agent_id=state.agent_id)
    if record:
        return {"status": "ok", "record": record}
    try:
        paths = store.get_cat_paths(state.agent_id)
        target = paths.get("prompts", store.PROMPTS_PATH)
        with target.open("r", encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
            if lines:
                return {"status": "ok", "record": json.loads(lines[-1])}
    except Exception:
        pass
    return {
        "status": "not_found",
        "record": None,
        "detail": "No prompt captured for latest tick.",
    }


@app.get("/api/agent/prompt/{tick}")
def get_prompt_for_tick(tick: int) -> Dict[str, Any]:
    record = store.read_prompt_for_tick(tick, agent_id=state.agent_id)
    if record:
        return {"status": "ok", "tick": tick, "record": record}

    size = store.prompt_log_size(agent_id=state.agent_id)
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
    paths = store.get_cat_paths(state.agent_id)
    return {"status": "ok", **store.prompt_log_size(agent_id=state.agent_id), "path": str(paths.get("prompts", store.PROMPTS_PATH))}


@app.get("/api/agent/coach/prompt/latest")
@app.get("/api/workbench/coach/prompt/latest")
def get_coach_prompt_latest(
    cat_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    target_id = cat_id or agent_id or state.agent_id or "cat-3"
    record = store.read_coach_prompt_for_tick(tick=None, agent_id=target_id)
    if record:
        return {"status": "ok", "cat_id": target_id, "record": record}
    return {
        "status": "not_found",
        "cat_id": target_id,
        "record": None,
        "detail": f"No coach prompts have been captured yet for {target_id}. The coach runs every N ticks or when triggered.",
    }


@app.get("/api/agent/coach/prompt/{tick}")
@app.get("/api/workbench/coach/prompt/{tick}")
def get_coach_prompt_for_tick(
    tick: int,
    cat_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    target_id = cat_id or agent_id or state.agent_id or "cat-3"
    record = store.read_coach_prompt_for_tick(tick=tick, agent_id=target_id)
    if record:
        return {"status": "ok", "tick": tick, "cat_id": target_id, "record": record}
    return {
        "status": "not_found",
        "tick": tick,
        "cat_id": target_id,
        "record": None,
        "detail": f"No coach prompt found for tick {tick} ({target_id}).",
    }


@app.get("/api/agent/coach/prompt-log/stats")
@app.get("/api/workbench/coach/prompt-log/stats")
def get_coach_prompt_log_stats(
    cat_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    target_id = cat_id or agent_id or state.agent_id or "cat-3"
    paths = store.get_cat_paths(target_id)
    p = paths.get("coach_prompts", store.COACH_PROMPTS_PATH)
    return {"status": "ok", "cat_id": target_id, **store.coach_prompt_log_size(target_id), "path": str(p)}


@app.get("/api/agent/directive")
def get_commander_directive() -> Dict[str, Any]:
    directive = state.commander_directive
    if not directive:
        return {"status": "ok", "directive": None, "age_ticks": None}
    issued = directive.get("tick")
    age = (state.active_tick - issued) if isinstance(issued, int) else None
    return {"status": "ok", "directive": directive, "age_ticks": age}


@app.delete("/api/agent/directive")
def clear_commander_directive() -> Dict[str, Any]:
    had = state.commander_directive is not None
    state.commander_directive = None
    return {"status": "ok", "cleared": had}


@app.get("/api/agent/evaluator/skills")
def get_evaluator_skills() -> Dict[str, str]:
    return {"content": get_eval_instruction(), "path": str(EVAL_SKILLS_PATH)}


@app.post("/api/agent/evaluator/run")
async def run_evaluator_now() -> Dict[str, Any]:
    if state.is_evaluating:
        return {"status": "busy", "detail": "An evaluation is already running."}
    state.eval_pending = False
    await _run_evaluation_cycle()
    return {"status": "ok", "result": state.last_eval_result}


@app.post("/api/agent/prompt", response_model=InterrogateResponse)
async def interrogate_agent(req: InterrogateRequest) -> InterrogateResponse:
    clean_prompt = req.prompt.strip()
    if not clean_prompt:
        return InterrogateResponse(
            reply="Commander, I received an empty transmission.",
            engine="heuristic_fallback",
            tick=state.active_tick,
            posture=state.latest_observation.get("self", {}) if state.latest_observation else {},
            active_strategy=(state.active_directive or {}).get("strategy", "STALKING"),
        )

    obs = None
    if state.latest_observation:
        try:
            obs = Observation(**state.latest_observation)
        except Exception:
            obs = None

    reply = f"[{state.agent_id}] Actor radio disabled (silent execution unit; speak to Evil Coach)."
    engine = "disabled"
    err = None

    self_info = obs.self if obs else {}
    is_dead = (self_info.get("alive") is False) or (self_info.get("hp", 150) <= 0)

    adopted = None
    if req.steer_directive and not is_dead:
        state.commander_directive = {
            "directive": clean_prompt,
            "tick": state.active_tick,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "source": "commander_radio",
        }
        adopted = clean_prompt
        state.latest_deliberated_decision = None
        state.latest_deliberated_prompt = None
        if state.deliberation_task and not state.deliberation_task.done():
            state.deliberation_task.cancel()
        state.is_deliberating = False

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
    strat = (state.active_directive or {}).get("strategy", "STALKING")

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


@app.post("/api/agent/general/prompt", response_model=GeneralInterrogateResponse)
async def interrogate_general_agent(
    req: GeneralInterrogateRequest,
) -> GeneralInterrogateResponse:
    clean_prompt = req.prompt.strip()
    notes = store.read_coaching_notes(agent_id=state.agent_id)
    notes_count = sum(
        1 for ln in (notes or "").splitlines() if ln.strip().startswith("-") or ln.strip().startswith("MODE:")
    )

    if not clean_prompt:
        return GeneralInterrogateResponse(
            reply="Commander, I received an empty transmission.",
            engine="offline_fallback",
            tick=state.active_tick,
            notes_in_force=notes_count,
        )

    existing = get_coach_directive() or {}
    try:
        all_records = store.read_transcript_records(agent_id=state.agent_id)
        window = max(1, int(state.eval_interval or 1))
        records = all_records[-window:] if all_records else None
    except Exception:
        records = None

    reply, engine, err = await interrogate_general(
        user_prompt=clean_prompt,
        notes=notes,
        records=records,
        standing_order=existing.get("directive"),
        tick=state.active_tick,
        agent_id=state.agent_id,
    )

    adopted = None
    if req.steer_directive:
        set_coach_directive(clean_prompt, tick=state.active_tick)
        adopted = clean_prompt

    now_str = datetime.now().strftime("%H:%M:%S")
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
    state.general_chat_history.clear()
    return {"status": "ok"}


@app.get("/api/agent/general/directive")
def get_general_directive() -> Dict[str, Any]:
    directive = get_coach_directive()
    return {
        "active": directive is not None,
        "directive": directive,
        "skills_path": str(GENERAL_RADIO_SKILLS_PATH),
        "timeout_sec": GENERAL_RADIO_TIMEOUT_SECONDS,
    }


@app.post("/api/agent/general/clear-directive")
def clear_general_directive() -> Dict[str, Any]:
    had = clear_coach_directive()
    return {"status": "ok", "cleared": had}


# Mount static assets and serve Evil Cat workbench dashboard from actors/evil_cat/static
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.head("/")
@app.get("/")
async def get_workbench():
    return FileResponse(static_dir / "index.html")


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">😈</text></svg>"""


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8005"))
    print(f"\n=======================================================", flush=True)
    print(f"😈 Evil Cat Agent Service & Interactive Workbench Online", flush=True)
    print(f"   • Brain: Google ADK ({ACTOR_MODEL}) with Evil Skills Handbook", flush=True)
    print(f"   • Workbench Dashboard: http://{host}:{port}", flush=True)
    print(f"   • API Observation Endpoint: http://{host}:{port}/act", flush=True)
    print(f"=======================================================\n", flush=True)
    uvicorn.run("actors.evil_cat.api:app", host=host, port=port, reload=True, log_level="warning")
