import asyncio
from datetime import datetime
import os
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
import httpx
import uvicorn

# Load local .env if present
load_dotenv()

# Ensure GOOGLE_CLOUD_LOCATION is populated from GOOGLE_CLOUD_REGION if present
if "GOOGLE_CLOUD_REGION" in os.environ and "GOOGLE_CLOUD_LOCATION" not in os.environ:
    os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ["GOOGLE_CLOUD_REGION"]

# Google Agent Development Kit (ADK) & GenAI
from google.adk.agents import LlmAgent
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

app = FastAPI(title="Cat Agent Service & Workbench")


# Security Headers Middleware
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
            "connect-src 'self';"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)


class Observation(BaseModel):
    tick: int
    round: int
    self: Dict[str, Any]
    visible_entities: List[Dict[str, Any]] = []
    legal_actions: List[str] = []


class ActionResponse(BaseModel):
    action: str
    thought: str = ""


class OverrideRequest(BaseModel):
    action: Optional[str] = None
    mode: Optional[str] = "once"


class ApiKeyRequest(BaseModel):
    api_key: str


class CatTacticalDecision(BaseModel):
    action: str = Field(description="The chosen action strictly from legal_actions")
    thought: str = Field(description="Concise tactical reasoning based on game skills and rules")
    target_mouse_id: Optional[str] = Field(default="", description="ID of targeted mouse or empty string if none")
    strategy: str = Field(default="ROAMING", description="Tactical phase: ATTACK, LUNGE, STALKING, or ROAMING")


# Skills manual path and loader
SKILLS_PATH = Path(__file__).parent / "skills.md"


def get_skills_instruction() -> str:
    if SKILLS_PATH.exists():
        return SKILLS_PATH.read_text(encoding="utf-8")
    return (
        "You are an expert tactical cat in a Cat and Mouse grid world. "
        "Your mission is to hunt mice, close distance with lunges, and eliminate them with attacks."
    )


# Initialize ADK Agent and Runner
adk_agent = LlmAgent(
    name="cat_tactician",
    model="gemini-2.5-flash",
    instruction=get_skills_instruction(),
    output_schema=CatTacticalDecision,
    output_key="decision",
)
session_service = InMemorySessionService()
adk_runner = Runner(
    agent=adk_agent,
    app_name="cat_agent_app",
    session_service=session_service,
)


class AgentWorkbenchState:
    def __init__(self, max_history: int = 50):
        self.max_history = max_history
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
        # ADK Gemini Engine state
        self.brain_engine: str = "Google ADK (gemini-2.5-flash)"
        self.skills_file: str = str(SKILLS_PATH)
        self.last_engine_source: str = "heuristic_fallback"
        self.last_engine_error: Optional[str] = None


# Singleton in-memory state
state = AgentWorkbenchState()


def compute_autonomous_decision(obs: Observation):
    self_info = obs.self
    my_id = self_info.get("id", "cat")
    pos = self_info.get("position", {"x": 0, "y": 0})
    facing = self_info.get("facing", "north")
    legal = obs.legal_actions

    # 1. Can we slash attack?
    mice_seen = [e for e in obs.visible_entities if e.get("type") == "mouse"]

    if "attack" in legal and mice_seen:
        # Check if any mouse is directly ahead within distance <= 2
        for m in mice_seen:
            m_pos = m.get("position", {})
            mx, my = m_pos.get("x", 0), m_pos.get("y", 0)
            dx = mx - pos["x"]
            dy = my - pos["y"]

            in_front = False
            if facing == "north" and dy < 0 and abs(dx) <= 1:
                in_front = True
            elif facing == "south" and dy > 0 and abs(dx) <= 1:
                in_front = True
            elif facing == "east" and dx > 0 and abs(dy) <= 1:
                in_front = True
            elif facing == "west" and dx < 0 and abs(dy) <= 1:
                in_front = True

            dist = abs(dx) + abs(dy)
            if in_front and dist <= 2:
                msg = f"Target {m.get('id')} in strike zone! Executing slash attack."
                meta = {
                    "phase": "ATTACK",
                    "target_id": m.get("id"),
                    "distance": dist,
                    "target_pos": {"x": mx, "y": my},
                }
                return "attack", msg, meta

    # 2. Can we lunge forward 2 squares to close distance on target?
    if "lunge" in legal and mice_seen:
        for m in mice_seen:
            m_pos = m.get("position", {})
            mx, my = m_pos.get("x", 0), m_pos.get("y", 0)
            dx = mx - pos["x"]
            dy = my - pos["y"]

            directly_ahead = False
            if facing == "north" and dy < 0 and dx == 0:
                directly_ahead = True
            elif facing == "south" and dy > 0 and dx == 0:
                directly_ahead = True
            elif facing == "east" and dx > 0 and dy == 0:
                directly_ahead = True
            elif facing == "west" and dx < 0 and dy == 0:
                directly_ahead = True

            dist = abs(dx) + abs(dy)
            if directly_ahead and dist >= 3:
                msg = f"Lunging 2 squares forward to close distance on {m.get('id')}!"
                meta = {
                    "phase": "LUNGE",
                    "target_id": m.get("id"),
                    "distance": dist,
                    "target_pos": {"x": mx, "y": my},
                }
                return "lunge", msg, meta

    # 3. Advance towards nearest seen mouse
    if mice_seen:
        target = min(
            mice_seen,
            key=lambda m: (m["position"]["x"] - pos["x"]) ** 2 + (m["position"]["y"] - pos["y"]) ** 2,
        )
        tx = target["position"]["x"]
        ty = target["position"]["y"]
        dx = tx - pos["x"]
        dy = ty - pos["y"]
        dist = abs(dx) + abs(dy)

        moves = []
        if abs(dx) >= abs(dy):
            if dx > 0 and "east" in legal: moves.append("east")
            elif dx < 0 and "west" in legal: moves.append("west")
            if dy > 0 and "south" in legal: moves.append("south")
            elif dy < 0 and "north" in legal: moves.append("north")
        else:
            if dy > 0 and "south" in legal: moves.append("south")
            elif dy < 0 and "north" in legal: moves.append("north")
            if dx > 0 and "east" in legal: moves.append("east")
            elif dx < 0 and "west" in legal: moves.append("west")

        if moves:
            choice = moves[0]
            msg = f"Advancing towards {target.get('id')}"
            meta = {
                "phase": "STALKING",
                "target_id": target.get("id"),
                "distance": dist,
                "target_pos": {"x": tx, "y": ty},
            }
            return choice, msg, meta

    # 4. Explore
    movement_choices = [a for a in legal if a in ["north", "south", "east", "west", "stay"]]
    choice = random.choice(movement_choices) if movement_choices else "stay"
    meta = {
        "phase": "ROAMING",
        "target_id": None,
        "distance": None,
    }
    return choice, "Exploring territory", meta


def compute_resulting_posture(pos: Dict[str, Any], facing: str, action: str) -> Dict[str, Any]:
    x = pos.get("x", 0)
    y = pos.get("y", 0)
    new_facing = facing
    new_x, new_y = x, y

    if action == "north":
        new_facing = "north"
        new_y = max(0, y - 1)
    elif action == "south":
        new_facing = "south"
        new_y = y + 1
    elif action == "east":
        new_facing = "east"
        new_x = x + 1
    elif action == "west":
        new_facing = "west"
        new_x = max(0, x - 1)
    elif action == "lunge":
        if facing == "north":
            new_y = max(0, y - 2)
        elif facing == "south":
            new_y = y + 2
        elif facing == "east":
            new_x = x + 2
        elif facing == "west":
            new_x = max(0, x - 2)

    return {
        "position": {"x": new_x, "y": new_y},
        "facing": new_facing,
    }


def run_sync(coro):
    """Executes a coroutine synchronously, safely handling existing running event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


async def reset_adk_session(agent_id: str):
    session_id = f"session_{agent_id}"
    try:
        await session_service.delete_session(
            app_name="cat_agent_app", user_id="cat_commander", session_id=session_id
        )
    except Exception:
        pass


async def query_adk_gemini(obs: Observation) -> Optional[CatTacticalDecision]:
    """Queries Gemini via Google ADK using the skills handbook.

    Returns CatTacticalDecision if successful, or None to fall back to heuristics.
    """
    # Skip live network calls when running automated pytest suite unless explicitly enabled
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        state.last_engine_source = "heuristic_fallback"
        return None

    has_env_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    has_cloud_auth = bool(os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"))

    if not (has_env_key or has_cloud_auth):
        state.last_engine_source = "heuristic_fallback"
        return None

    user_id = "cat_commander"
    session_id = f"session_{state.agent_id}"

    try:
        session = await session_service.get_session(
            app_name="cat_agent_app", user_id=user_id, session_id=session_id
        )
        if session is None:
            session = await session_service.create_session(
                app_name="cat_agent_app", user_id=user_id, session_id=session_id
            )

        pos = obs.self.get("position", {})
        facing = obs.self.get("facing", "north")
        hp = obs.self.get("hp", 100)
        entities = obs.visible_entities
        legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]

        prompt = (
            f"--- OBSERVATION (Tick {obs.tick}, Round {obs.round}) ---\n"
            f"Agent ID: {obs.self.get('id', state.agent_id)}\n"
            f"Current Position: (x={pos.get('x', 0)}, y={pos.get('y', 0)})\n"
            f"Current Facing: {facing}\n"
            f"Current HP: {hp}\n"
            f"Visible Entities in 90° Vision Cone: {entities}\n"
            f"Legal Actions this tick: {legal}\n"
            f"Consult your skills manual and choose the single best tactical action. "
            f"Your action MUST strictly be one of {legal}."
        )

        msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        decision = None

        async with asyncio.timeout(5.0):
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

        if decision and decision.action:
            chosen_clean = decision.action.strip().lower()
            if chosen_clean in legal:
                decision.action = chosen_clean
                state.last_engine_source = "adk_gemini"
                state.last_engine_error = None
                return decision
            else:
                print(f"[ADK Warning] Model selected '{chosen_clean}' which is not in {legal}", flush=True)

    except Exception as exc:
        err_msg = str(exc)
        state.last_engine_source = "heuristic_fallback"
        state.last_engine_error = err_msg
        print(f"[ADK Fallback] {type(exc).__name__}: {err_msg[:100]} -> using heuristic brain", flush=True)

    return None


@app.post("/act", response_model=ActionResponse)
@app.post("/", response_model=ActionResponse)
def act(obs: Observation) -> ActionResponse:
    my_id = obs.self.get("id", "cat")

    # 1. Evaluate ADK Gemini decision if credentials exist
    adk_decision = None
    has_env_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    has_cloud_auth = bool(os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"))
    if has_env_key or has_cloud_auth:
        adk_decision = run_sync(query_adk_gemini(obs))

    if adk_decision:
        auto_action = adk_decision.action
        auto_thought = adk_decision.thought
        auto_meta = {
            "phase": adk_decision.strategy or "TACTICAL",
            "target_id": adk_decision.target_mouse_id or None,
            "engine": "adk_gemini",
        }
        source_engine = "adk_gemini"
    else:
        auto_action, auto_thought, auto_meta = compute_autonomous_decision(obs)
        auto_meta["engine"] = "heuristic_fallback"
        source_engine = "heuristic_fallback"

    # 2. Check for manual override
    if state.pending_override and state.pending_override in (obs.legal_actions or ["north", "south", "east", "west", "stay", "attack", "lunge"]):
        chosen_action = state.pending_override
        chosen_thought = f"Manual override ({state.override_mode}): {chosen_action}"
        source = "manual_override"
        print(f"[{my_id} @ Tick {obs.tick}] MANUAL OVERRIDE -> {chosen_action}", flush=True)

        if state.override_mode == "once":
            state.pending_override = None
    elif state.pending_override:
        # Override requested action not in legal actions
        chosen_action = auto_action
        chosen_thought = f"Override '{state.pending_override}' illegal; reverted to {source_engine}: {auto_thought}"
        source = source_engine
        print(f"[{my_id} @ Tick {obs.tick}] Override illegal ({state.pending_override}) -> {auto_action}", flush=True)
        if state.override_mode == "once":
            state.pending_override = None
    else:
        chosen_action = auto_action
        chosen_thought = auto_thought
        source = source_engine
        print(f"[{my_id} @ Tick {obs.tick}] [{source_engine.upper()}] {auto_meta.get('phase', 'MOVE')} -> {chosen_action}", flush=True)

    # Record exchange with input observation and resulting posture
    obs_dict = obs.model_dump()
    posture = compute_resulting_posture(
        pos=obs.self.get("position", {"x": 0, "y": 0}),
        facing=obs.self.get("facing", "north"),
        action=chosen_action,
    )

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

    # Update latest observation to capture the move that was just applied (prevents 1-tick delay)
    current_obs = dict(obs_dict)
    current_obs["self"] = dict(obs_dict.get("self", {}))
    current_obs["self"]["position"] = posture["position"]
    current_obs["self"]["facing"] = posture["facing"]

    state.latest_observation = current_obs
    state.latest_response = exchange["response"]
    state.latest_autonomous = exchange["autonomous_decision"]
    state.active_tick = obs.tick
    state.total_ticks_processed += 1

    state.history.append(exchange)
    if len(state.history) > state.max_history:
        state.history.pop(0)

    return ActionResponse(action=chosen_action, thought=chosen_thought)


GAME_SERVER_URL = "http://127.0.0.1:8000"


async def sync_live_observation(agent_id: Optional[str] = None) -> bool:
    target_id = agent_id or state.agent_id or "cat-1"
    try:
        async with httpx.AsyncClient(timeout=0.35) as client:
            resp = await client.get(f"{GAME_SERVER_URL}/api/simulations/active/agents/{target_id}/observation")
            if resp.status_code == 200:
                data = resp.json()
                sim_id = data.get("simulation_id")
                server_tick = data.get("tick", 0)

                # Reset history if simulation restarted or rewound
                if state.simulation_id and sim_id and state.simulation_id != sim_id:
                    state.history.clear()
                    state.total_ticks_processed = 0
                    await reset_adk_session(target_id)
                state.simulation_id = sim_id

                last_history_tick = state.history[-1]["tick"] if state.history else 0
                if server_tick < last_history_tick:
                    state.history.clear()
                    state.total_ticks_processed = 0
                    last_history_tick = 0
                    await reset_adk_session(target_id)

                # Compute upcoming tick:
                # If last history action matches or exceeds server_tick, board is ready for the upcoming tick
                if state.history and last_history_tick >= server_tick:
                    upcoming_tick = last_history_tick + 1
                elif server_tick == 0 and not state.history:
                    upcoming_tick = 1
                else:
                    upcoming_tick = server_tick

                self_info = data.get("self", {})
                live_obs = {
                    "tick": upcoming_tick,
                    "round": data.get("round", 0),
                    "self": {
                        "id": self_info.get("id", target_id),
                        "type": self_info.get("type", "cat"),
                        "position": self_info.get("position", {"x": 0, "y": 0}),
                        "facing": self_info.get("facing", "north"),
                        "hp": self_info.get("hp", 100),
                    },
                    "visible_entities": data.get("visible_entities", []),
                    "legal_actions": data.get("legal_actions", ["stay", "north", "south", "east", "west", "attack", "lunge"]),
                }

                state.agent_id = target_id
                state.latest_observation = live_obs
                state.active_tick = upcoming_tick

                # Evaluate proposed autonomous action for this live observation
                live_obs_obj = Observation(**live_obs)
                adk_dec = await query_adk_gemini(live_obs_obj)
                if adk_dec:
                    state.latest_autonomous = {
                        "action": adk_dec.action,
                        "thought": adk_dec.thought,
                        "meta": {
                            "phase": adk_dec.strategy or "TACTICAL",
                            "target_id": adk_dec.target_mouse_id or None,
                            "engine": "adk_gemini",
                        },
                    }
                else:
                    auto_action, auto_thought, auto_meta = compute_autonomous_decision(live_obs_obj)
                    auto_meta["engine"] = "heuristic_fallback"
                    state.latest_autonomous = {
                        "action": auto_action,
                        "thought": auto_thought,
                        "meta": auto_meta,
                    }
                return True
    except Exception:
        pass
    return False


@app.get("/api/agent/state")
async def get_agent_state(sync: bool = False, agent_id: Optional[str] = None) -> Dict[str, Any]:
    if sync:
        await sync_live_observation(agent_id)

    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    return {
        "connected": state.latest_observation is not None,
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
        "has_api_key": has_key,
        "last_engine_source": state.last_engine_source,
        "last_engine_error": state.last_engine_error,
    }


@app.post("/api/agent/set-key")
def set_api_key(req: ApiKeyRequest) -> Dict[str, Any]:
    key = req.api_key.strip()
    if key:
        os.environ["GEMINI_API_KEY"] = key
        state.last_engine_error = None
        return {"status": "ok", "has_api_key": True}
    else:
        os.environ.pop("GEMINI_API_KEY", None)
        return {"status": "cleared", "has_api_key": False}


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


@app.post("/api/agent/clear-history")
def clear_history() -> Dict[str, str]:
    state.history.clear()
    return {"status": "ok"}


# Mount static assets and serve workbench dashboard
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def get_workbench():
    return FileResponse(static_dir / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    print(f"\n=======================================================", flush=True)
    print(f"🐱 Cat Agent Service & Interactive Workbench Online", flush=True)
    print(f"   • Brain: Google ADK (gemini-2.5-flash) with Skills Handbook", flush=True)
    print(f"   • Workbench Dashboard: http://127.0.0.1:{port}", flush=True)
    print(f"   • API Observation Endpoint: http://127.0.0.1:{port}/act", flush=True)
    print(f"=======================================================\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
