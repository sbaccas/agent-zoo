import random
import sys
from typing import Any, Dict, List
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Mouse Agent Service")


class Observation(BaseModel):
    tick: int
    round: int
    self: Dict[str, Any]
    visible_entities: List[Dict[str, Any]] = []
    legal_actions: List[str] = []


class ActionResponse(BaseModel):
    action: str
    thought: str = ""


@app.post("/act", response_model=ActionResponse)
def act(obs: Observation) -> ActionResponse:
    self_info = obs.self
    my_id = self_info.get("id", "mouse")
    pos = self_info.get("position", {"x": 0, "y": 0})
    legal = [a for a in obs.legal_actions if a in ["north", "south", "east", "west", "stay"]]

    # Check for predator threats in 360 field of view
    cats_seen = [e for e in obs.visible_entities if e.get("type") == "cat"]

    if cats_seen:
        threat = min(
            cats_seen,
            key=lambda c: (c["position"]["x"] - pos["x"]) ** 2 + (c["position"]["y"] - pos["y"]) ** 2
        )
        cx = threat["position"]["x"]
        cy = threat["position"]["y"]
        dx = pos["x"] - cx
        dy = pos["y"] - cy

        # Flee directly away from threat
        flee_moves = []
        if abs(dx) >= abs(dy):
            if dx > 0 and "east" in legal: flee_moves.append("east")
            elif dx < 0 and "west" in legal: flee_moves.append("west")
            if dy > 0 and "south" in legal: flee_moves.append("south")
            elif dy < 0 and "north" in legal: flee_moves.append("north")
        else:
            if dy > 0 and "south" in legal: flee_moves.append("south")
            elif dy < 0 and "north" in legal: flee_moves.append("north")
            if dx > 0 and "east" in legal: flee_moves.append("east")
            elif dx < 0 and "west" in legal: flee_moves.append("west")

        if flee_moves:
            choice = flee_moves[0]
            print(f"[{my_id} @ Tick {obs.tick}] ⚠️ FLEEING from {threat.get('id')} -> {choice}", flush=True)
            return ActionResponse(action=choice, thought=f"Fleeing from {threat.get('id')}")

    # Roam/forage
    choice = random.choice(legal) if legal else "stay"
    print(f"[{my_id} @ Tick {obs.tick}] Foraging -> {choice}", flush=True)
    return ActionResponse(action=choice, thought="Foraging peacefully")



if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
    print(f"🐭 Mouse Agent Service running on http://127.0.0.1:{port} (waiting for observations on /act)...", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

