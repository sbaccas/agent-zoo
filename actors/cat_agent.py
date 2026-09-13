from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
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
    HistoryWindowRequest,
    InterrogateRequest,
    InterrogateResponse,
    Observation,
    OverrideRequest,
)

# Heuristic tactical engine
from actors.cat_heuristic import (
    compute_heuristic_decision,
    compute_reflex_decision,
    compute_resulting_posture,
)

# Model / LLM tactical engine
from actors.cat_model import (
    SKILLS_PATH,
    adk_agent,
    adk_runner,
    check_gemini_auth_health,
    get_gemini_auth_mode,
    get_skills_instruction,
    has_gemini_credentials,
    interrogate_adk_gemini,
    is_gemini_authenticated,
    mark_auth_error,
    query_adk_gemini as model_query_adk_gemini,
    reset_adk_session,
    session_service,
)

logger = logging.getLogger("cat_agent")

# Backwards compatibility alias for heuristic decision function
compute_autonomous_decision = compute_heuristic_decision


class AgentWorkbenchState:
    """In-memory state and history store for the Cat Agent."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self.history_window_size: int = 3
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
        self.brain_engine: str = "Google ADK (gemini-2.5-flash)"
        self.skills_file: str = str(SKILLS_PATH)
        initial_health = check_gemini_auth_health()
        self.last_engine_source: str = "adk_gemini" if initial_health.get("authenticated") else "heuristic_fallback"
        self.last_engine_error: Optional[str] = initial_health.get("error") if not initial_health.get("authenticated") else None
        self.last_evaluated_tick: Optional[int] = None

        # Decoupled Deliberation & System 1/2 Architecture (AI-02)
        self.active_directive: Optional[Dict[str, Any]] = None
        self.deliberation_task: Optional[asyncio.Task] = None
        self.latest_deliberated_decision: Optional[CatTacticalDecision] = None
        self.is_deliberating: bool = False


# Singleton in-memory state
state = AgentWorkbenchState()


async def query_adk_gemini(obs: Observation) -> Optional[CatTacticalDecision]:
    """Wraps model execution, updating agent workbench diagnostic state."""
    decision, source, err = await model_query_adk_gemini(
        obs,
        history=state.history,
        agent_id=state.agent_id,
        history_window_size=state.history_window_size,
    )
    state.last_engine_source = source
    state.last_engine_error = err
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
        decision, source, err = await model_query_adk_gemini(
            obs,
            history=state.history,
            agent_id=state.agent_id,
            history_window_size=state.history_window_size,
        )
        state.last_engine_source = source
        state.last_engine_error = err
        if err:
            mark_auth_error(err)
        if decision:
            state.latest_deliberated_decision = decision
            state.active_directive = {
                "strategy": decision.strategy,
                "target_mouse_id": decision.target_mouse_id,
                "thought": decision.thought,
                "action": decision.action,
                "tick": obs.tick,
            }
    except Exception as exc:
        err_msg = str(exc)
        state.last_engine_error = err_msg
        state.last_engine_source = "heuristic_fallback"
        mark_auth_error(err_msg)
    finally:
        state.is_deliberating = False


async def act_async(obs: Observation, decoupled: bool = False) -> ActionResponse:
    """Evaluates an observation, arbitrating between manual override, LLM, and fast reflex."""
    obs_dict = obs.model_dump()
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]

    active_dir = state.active_directive or {}
    locked_target = active_dir.get("target_mouse_id") or None
    active_strat = active_dir.get("strategy") or "ROAMING"

    auth_health = check_gemini_auth_health()
    is_authenticated = bool(auth_health.get("authenticated", False))
    if is_authenticated:
        state.last_engine_error = None
    auth_err = None if is_authenticated else (auth_health.get("error") or state.last_engine_error)

    if decoupled and is_authenticated:
        # System 1 / System 2 Decoupled Deliberation (AI-02):
        # 1. Did a background Gemini deliberation task just complete?
        if state.latest_deliberated_decision and state.latest_deliberated_decision.action in legal:
            llm_decision = state.latest_deliberated_decision
            state.latest_deliberated_decision = None
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
            auto_action, auto_thought, auto_meta = compute_reflex_decision(
                obs, locked_target_id=locked_target, strategy=active_strat
            )
            auto_meta["engine"] = "adk_gemini"
            auto_meta["strategy"] = active_strat
            auto_meta["target_id"] = locked_target
            auto_meta["phase"] = active_strat
            state.last_engine_source = "adk_gemini"

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
        elif has_gemini_credentials() or auth_err:
            auto_meta["engine"] = "heuristic_fallback"
            state.last_engine_source = "heuristic_fallback"
            if not state.last_engine_error and auth_err:
                state.last_engine_error = auth_err
        else:
            auto_meta["engine"] = "heuristic"
            state.last_engine_source = "heuristic"

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

    # Calculate posture resulting from chosen action
    pos = obs.self.get("position", {"x": 0, "y": 0})
    facing = obs.self.get("facing", "north")
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
            "resulting_posture": posture,
        },
        "source": source,
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

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "connecting": self.connecting,
            "server_url": self.server_url,
            "assigned_id": self.assigned_id or state.agent_id or "cat-1",
            "requested_cat_id": self.requested_cat_id,
            "last_error": self.last_error,
            "auto_reconnect": self.auto_reconnect,
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
                            obs_dict = data.get("payload", {})
                            try:
                                obs_obj = Observation(**obs_dict)
                                action_resp = await act_async(obs_obj, decoupled=True)
                                meta = state.latest_autonomous.get("meta", {}) if state.latest_autonomous else {}
                                resp_msg = {
                                    "type": "action",
                                    "tick": obs_obj.tick,
                                    "action": action_resp.action,
                                    "thought": action_resp.thought,
                                    "strategy": meta.get("strategy", "ROAMING"),
                                    "target_mouse_id": meta.get("target_mouse_id", ""),
                                }
                                await ws.send(json.dumps(resp_msg))
                            except Exception as err:
                                print(f"⚠️ [WS CLIENT] Error computing action: {err}", flush=True)
                                await ws.send(json.dumps({
                                    "type": "action",
                                    "tick": data.get("tick", 0),
                                    "action": "stay",
                                    "thought": "Fallback stay action",
                                }))

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
                    state.total_ticks_processed = 0
                    state.last_evaluated_tick = None
                    await reset_adk_session(target_id)
                state.simulation_id = sim_id

                last_history_tick = state.history[-1]["tick"] if state.history else 0
                if server_tick < last_history_tick:
                    state.history.clear()
                    state.total_ticks_processed = 0
                    state.last_evaluated_tick = None
                    last_history_tick = 0
                    await reset_adk_session(target_id)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Suppress high-frequency polling from uvicorn.access logger and handlers
    filt = PollingLogFilter()
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(filt)
    for h in access_logger.handlers:
        h.addFilter(filt)

    # Auto-connect WebSocket client in background if enabled
    if os.environ.get("CAT_AGENT_AUTO_CONNECT", "1") != "0":
        asyncio.create_task(ws_client.connect())
    yield
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
    if is_auth:
        state.last_engine_error = None
        state.last_engine_source = "adk_gemini"
    auth_err = None if is_auth else (state.last_engine_error or auth_health.get("error"))

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
        "last_engine_error": auth_err,
        "is_deliberating": state.is_deliberating,
        "active_directive": state.active_directive,
        "history_window_size": state.history_window_size,
        "chat_history": state.chat_history[-50:],
    }


@app.post("/api/agent/auth/verify")
async def verify_auth_endpoint() -> Dict[str, Any]:
    """Force-checks authentication status and clears errors if reauthenticated."""
    health = check_gemini_auth_health(force_refresh=True)
    if health.get("authenticated"):
        state.last_engine_error = None
        state.last_engine_source = "adk_gemini"
    else:
        state.last_engine_error = health.get("error")
        state.last_engine_source = "heuristic_fallback"
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
        state.last_engine_error = None
        state.last_engine_source = "adk_gemini"
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


@app.post("/api/agent/clear-history")
def clear_history() -> Dict[str, str]:
    state.history.clear()
    return {"status": "ok"}


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
        active_directive=state.active_directive,
    )

    adopted = None
    if req.steer_directive:
        state.active_directive = {
            "strategy": "ROAMING",
            "directive": clean_prompt,
            "thought": f"Commander Directive: {clean_prompt}",
            "source": "commander_prompt",
            "tick": state.active_tick,
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
    uvicorn.run("actors.cat_agent:app", host="127.0.0.1", port=port, reload=True, log_level="warning")
