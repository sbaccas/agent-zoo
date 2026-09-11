from datetime import datetime
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
import httpx
import uvicorn

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

    # 2. Advance towards nearest seen mouse
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

    # 3. Explore
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

    return {
        "position": {"x": new_x, "y": new_y},
        "facing": new_facing,
    }


@app.post("/act", response_model=ActionResponse)
@app.post("/", response_model=ActionResponse)
def act(obs: Observation) -> ActionResponse:
    my_id = obs.self.get("id", "cat")
    auto_action, auto_thought, auto_meta = compute_autonomous_decision(obs)

    # Check for manual override
    if state.pending_override and state.pending_override in (obs.legal_actions or ["north", "south", "east", "west", "stay", "attack"]):
        chosen_action = state.pending_override
        chosen_thought = f"Manual override ({state.override_mode}): {chosen_action}"
        source = "manual_override"
        print(f"[{my_id} @ Tick {obs.tick}] MANUAL OVERRIDE -> {chosen_action}", flush=True)

        if state.override_mode == "once":
            state.pending_override = None
    elif state.pending_override:
        # Override requested action not in legal actions
        chosen_action = auto_action
        chosen_thought = f"Override '{state.pending_override}' illegal; reverted to auto: {auto_thought}"
        source = "autonomous"
        print(f"[{my_id} @ Tick {obs.tick}] Override illegal ({state.pending_override}) -> {auto_action}", flush=True)
        if state.override_mode == "once":
            state.pending_override = None
    else:
        chosen_action = auto_action
        chosen_thought = auto_thought
        source = "autonomous"
        print(f"[{my_id} @ Tick {obs.tick}] {auto_meta['phase']} -> {chosen_action}", flush=True)

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
                state.simulation_id = sim_id

                last_history_tick = state.history[-1]["tick"] if state.history else 0
                if server_tick < last_history_tick:
                    state.history.clear()
                    state.total_ticks_processed = 0
                    last_history_tick = 0

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
                    "legal_actions": data.get("legal_actions", ["stay", "north", "south", "east", "west", "attack"]),
                }

                state.agent_id = target_id
                state.latest_observation = live_obs
                state.active_tick = upcoming_tick

                # Evaluate proposed autonomous action for this live observation
                auto_action, auto_thought, auto_meta = compute_autonomous_decision(Observation(**live_obs))
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
    }


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
    print(f"   • Workbench Dashboard: http://127.0.0.1:{port}", flush=True)
    print(f"   • API Observation Endpoint: http://127.0.0.1:{port}/act", flush=True)
    print(f"=======================================================\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
