import random
import sys
from typing import Any, Dict, List
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Cat Agent Service")


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

            if in_front and (abs(dx) + abs(dy) <= 2):
                msg = f"Target {m.get('id')} in strike zone! Executing slash attack."
                print(f"[{my_id} @ Tick {obs.tick}] ATTACK -> {m.get('id')}", flush=True)
                return ActionResponse(action="attack", thought=msg)

    # 2. Advance towards nearest seen mouse
    if mice_seen:
        target = min(
            mice_seen,
            key=lambda m: (m["position"]["x"] - pos["x"]) ** 2 + (m["position"]["y"] - pos["y"]) ** 2
        )
        tx = target["position"]["x"]
        ty = target["position"]["y"]
        dx = tx - pos["x"]
        dy = ty - pos["y"]

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
            print(f"[{my_id} @ Tick {obs.tick}] Stalking towards {target.get('id')} -> {choice}", flush=True)
            return ActionResponse(action=choice, thought=f"Advancing towards {target.get('id')}")

    # 3. Explore
    movement_choices = [a for a in legal if a in ["north", "south", "east", "west", "stay"]]
    choice = random.choice(movement_choices) if movement_choices else "stay"
    print(f"[{my_id} @ Tick {obs.tick}] Roaming -> {choice}", flush=True)
    return ActionResponse(action=choice, thought="Exploring territory")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
