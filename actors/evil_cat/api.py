"""FastAPI Workbench Server & Agent Runner for Evil Cat (cat-3).

Runs on PORT 8005 by default.
Features:
- Fast tactical actor with 0 tool overhead.
- Asynchronous Evil Coach conductor running out-of-band at configurable intervals.
- Connects to Game Server via WebSocket /ws/agents as cat-3.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from actors.models import (
    ActionResponse,
    CatTacticalDecision,
    EvalIntervalRequest,
    Observation,
    OverrideRequest,
    ThinkingBudgetRequest,
)
from actors.cat.heuristic import compute_heuristic_decision
from actors.evil_cat.actor import (
    ACTOR_MODEL,
    SKILLS_PATH,
    adk_agent,
    build_decision_prompt,
    check_gemini_auth_health,
    get_skills_instruction,
    get_thinking_budget,
    has_gemini_credentials,
    query_adk_gemini,
    set_thinking_budget,
)
from actors.evil_cat.eval import (
    EVAL_MODEL,
    COACH_SKILLS_PATH,
    evaluate_and_publish,
    get_eval_instruction,
)
from actors.cat import store
from actors.a2a import store as a2a_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("evil_cat_api")

STATIC_DIR = Path(__file__).resolve().parent.parent / "cat" / "static"


class EvilWorkbenchState:
    def __init__(self):
        self.agent_id: str = os.environ.get("CAT_ID", "cat-3")
        self.simulation_id: Optional[str] = None
        self.active_tick: int = 0
        self.total_ticks_processed: int = 0
        self.latest_observation: Optional[Dict[str, Any]] = None
        self.latest_response: Optional[Dict[str, Any]] = None
        self.latest_autonomous: Optional[Dict[str, Any]] = None
        self.pending_override: Optional[str] = None
        self.override_mode: str = "once"
        self.history: List[Dict[str, Any]] = []
        self.max_history: int = 50
        self.history_window_size: int = 5

        # Asynchronous non-blocking coach evaluation settings
        self.eval_enabled: bool = True
        self.eval_interval: int = int(os.environ.get("CAT_EVAL_INTERVAL", "5"))
        self.last_eval_dispatch_tick: int = -999
        self.eval_pending: bool = False
        self.is_evaluating: bool = False
        self.last_eval_result: Optional[Dict[str, Any]] = None

        self.last_prompt_capture: Optional[Dict[str, Any]] = None
        self.is_deliberating: bool = False
        self.buffered_decision: Optional[CatTacticalDecision] = None
        self.buffered_source: Optional[str] = None
        self.buffered_meta: Optional[Dict[str, Any]] = None
        self.buffered_tick: Optional[int] = None


state = EvilWorkbenchState()


async def _run_evaluation_cycle():
    """Runs out-of-band evaluation cycle in the background."""
    if state.is_evaluating:
        return
    try:
        state.is_evaluating = True
        result = await evaluate_and_publish(agent_id=state.agent_id)
        state.last_eval_result = result
    except Exception as exc:
        logger.warning("Evil Coach cycle failed: %s", exc)
    finally:
        state.is_evaluating = False


def maybe_dispatch_evaluation(current_tick: int, force: bool = False) -> None:
    if not state.eval_enabled or state.is_evaluating:
        return
    interval = max(1, state.eval_interval)
    if not force and (current_tick - state.last_eval_dispatch_tick < interval):
        return
    state.last_eval_dispatch_tick = current_tick
    asyncio.create_task(_run_evaluation_cycle())
    logger.info("Dispatched non-blocking Evil Coach evaluation at tick %s (interval=%s, force=%s)", current_tick, interval, force)


async def act_async(obs: Observation) -> ActionResponse:
    """Processes a game tick with fast deliberation and reflex fallback."""
    state.latest_observation = obs.model_dump()
    state.active_tick = obs.tick

    capture: Dict[str, Any] = {}
    chosen_action = "stay"
    chosen_thought = ""
    source = "heuristic_fallback"
    auto_meta: Dict[str, Any] = {}

    # Check for manual operator override
    if state.pending_override and state.pending_override in obs.legal_actions:
        chosen_action = state.pending_override
        chosen_thought = f"[OPERATOR OVERRIDE] Action {chosen_action} enforced manually."
        source = "operator_override"
        if state.override_mode == "once":
            state.pending_override = None
    elif state.buffered_decision and state.buffered_decision.action in obs.legal_actions:
        chosen_action = state.buffered_decision.action
        chosen_thought = state.buffered_decision.thought
        source = state.buffered_source or "adk_gemini"
        auto_meta = state.buffered_meta or {}
        state.buffered_decision = None
    else:
        # Fallback to reflex
        action, thought, meta = compute_heuristic_decision(obs)
        chosen_action = action
        chosen_thought = thought
        source = "heuristic_fallback"
        auto_meta = meta

    # Trigger background deliberation for the next tick
    async def _deliberate_next():
        try:
            state.is_deliberating = True
            dec, s, meta = await query_adk_gemini(
                obs=obs,
                history=state.history,
                agent_id=state.agent_id,
                history_window_size=state.history_window_size,
                capture=capture,
            )
            state.buffered_decision = dec
            state.buffered_source = s
            state.buffered_meta = meta
            state.buffered_tick = obs.tick
            state.last_prompt_capture = capture
        finally:
            state.is_deliberating = False

    asyncio.create_task(_deliberate_next())

    exchange = {
        "tick": obs.tick,
        "round": obs.round,
        "observation": obs.model_dump(),
        "autonomous_decision": {
            "action": chosen_action,
            "thought": chosen_thought,
            "source": source,
            "meta": auto_meta,
        },
        "response": {"action": chosen_action, "thought": chosen_thought},
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    state.latest_response = exchange["response"]
    state.latest_autonomous = exchange["autonomous_decision"]
    state.total_ticks_processed += 1
    state.history.append(exchange)
    if len(state.history) > state.max_history:
        state.history.pop(0)

    store.append_tick(exchange)

    # Check if a new rival cat was spotted in FOV that warrants calling the Slow Brain to redirect
    force_eval = False
    spotted_rivals = [
        e.get("id") for e in (obs.visible_entities or [])
        if e.get("type") == "cat" and e.get("id") != state.agent_id
    ]
    if spotted_rivals:
        current_notes = store.read_coaching_notes(agent_id=state.agent_id) or ""
        # If any spotted rival is not mentioned in the active coaching notes, request immediate re-evaluation!
        for rival in spotted_rivals:
            if rival and rival not in current_notes:
                force_eval = True
                logger.info("Rival cat %s spotted in FOV; requesting immediate Slow Brain re-evaluation!", rival)
                break

    maybe_dispatch_evaluation(obs.tick, force=force_eval)

    return ActionResponse(action=chosen_action, thought=chosen_thought)


class WsClientManager:
    """Maintains persistent WebSocket connection to the Game Server."""

    def __init__(self):
        default_url = os.environ.get("GAME_SERVER_WS_URL") or "ws://127.0.0.1:8000/ws/agents"
        if default_url.startswith("http://"):
            default_url = "ws://" + default_url[7:]
        elif default_url.startswith("https://"):
            default_url = "wss://" + default_url[8:]
        if "/ws/agents" not in default_url:
            default_url = default_url.rstrip("/") + "/ws/agents"

        self.server_url: str = default_url
        self.requested_cat_id: str = state.agent_id
        self.connected: bool = False
        self.running: bool = True
        self._task: Optional[asyncio.Task] = None

    def start(self):
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self):
        while self.running:
            try:
                url = f"{self.server_url}?agent_id={self.requested_cat_id}&type=cat"
                logger.info("Evil Cat connecting to Game Server: %s", url)
                async with websockets.connect(url) as ws:
                    self.connected = True
                    logger.info("✓ Evil Cat connected to Game Server!")
                    async for raw in ws:
                        data = json.loads(raw)
                        msg_type = data.get("type", "observation")
                        if msg_type == "observation":
                            obs_data = data.get("observation", data)
                            obs = Observation.model_validate(obs_data)
                            resp = await act_async(obs)
                            await ws.send(json.dumps({
                                "type": "action",
                                "action": resp.action,
                                "thought": resp.thought,
                            }))
                        elif msg_type == "reset":
                            store.reset_transcript(state.agent_id)
                            store.reset_coaching_notes(state.agent_id)
                            state.history.clear()
            except Exception as exc:
                self.connected = False
                logger.warning("Evil Cat WS connection error: %s. Reconnecting in 2s...", exc)
                await asyncio.sleep(2.0)


ws_manager = WsClientManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_manager.start()
    yield
    ws_manager.running = False


app = FastAPI(title="Evil Cat Workbench (cat-3)", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"status": "ok", "agent": "evil-cat", "id": state.agent_id})


@app.post("/act", response_model=ActionResponse)
async def act_endpoint(obs: Observation):
    return await act_async(obs)


@app.get("/api/workbench/status")
async def get_status():
    auth_health = check_gemini_auth_health()
    return {
        "agent_id": state.agent_id,
        "active_tick": state.active_tick,
        "connected": ws_manager.connected,
        "authenticated": auth_health.get("authenticated", False),
        "auth_mode": auth_health.get("mode", "unknown"),
        "thinking_budget": get_thinking_budget(),
        "eval_interval": state.eval_interval,
        "is_evaluating": state.is_evaluating,
        "latest_observation": state.latest_observation,
        "latest_response": state.latest_response,
        "latest_standing_directive": store.read_coaching_notes(agent_id=state.agent_id),
    }


@app.post("/api/workbench/eval-interval")
async def set_interval(req: EvalIntervalRequest):
    state.eval_interval = max(1, req.interval)
    return {"status": "ok", "eval_interval": state.eval_interval}


@app.post("/api/workbench/thinking-budget")
async def update_budget(req: ThinkingBudgetRequest):
    budget = set_thinking_budget(req.thinking_budget)
    return {"status": "ok", "thinking_budget": budget}


@app.get("/api/workbench/prompt/latest")
async def get_prompt_latest():
    return state.last_prompt_capture or {}


@app.post("/api/workbench/override")
async def override_action(req: OverrideRequest):
    state.pending_override = req.action
    state.override_mode = req.mode or "once"
    return {"status": "ok", "action": req.action, "mode": state.override_mode}


@app.post("/api/workbench/reset")
async def reset_agent():
    store.reset_transcript(state.agent_id)
    store.reset_coaching_notes(state.agent_id)
    state.history.clear()
    return {"status": "ok", "reset": True}
