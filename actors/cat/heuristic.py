import random
from typing import Any, Dict, Optional, Tuple
from actors.models import Observation


def compute_heuristic_decision(obs: Observation) -> Tuple[str, str, Dict[str, Any]]:
    """
    Evaluates an observation using deterministic tactical heuristics:
      1. Slash attack if a mouse is in the forward strike zone -- the
         3-wide rectangle extending `attack_range` tiles ahead, per
         `in_strike_zone`.
      2. High-speed lunge if a mouse is aligned directly ahead (distance >= 3).
      3. Stalk / pursue nearest visible mouse along Manhattan shortest path.
      4. Roam / explore legal directions when no prey is visible.

    Returns:
      (action_name, thought_string, metadata_dict)
    """
    self_info = obs.self
    pos = self_info.get("position", {"x": 0, "y": 0})
    facing = self_info.get("facing", "north")
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]

    mice_seen = [e for e in obs.visible_entities if e.get("type") == "mouse"]

    # 1. Can we slash attack?
    #
    # Membership is delegated to in_strike_zone, which mirrors the engine's
    # World.get_slash_aoe_tiles exactly. This function previously carried its
    # own copy of the test using `abs(dx) + abs(dy) <= atk_range`, which is a
    # diamond, not the engine's rectangle: it declined the two deepest
    # off-axis tiles of every facing (at range 2, a third of the zone) because
    # their Manhattan sum is atk_range + 1. Those are corner tiles -- exactly
    # where a mouse fleeing at an angle ends up -- so the miss cost real kills.
    atk_range = (obs.capabilities or {}).get("attack_range", 2)
    if "attack" in legal and mice_seen:
        for m in mice_seen:
            m_pos = m.get("position", {})
            mx, my = m_pos.get("x", 0), m_pos.get("y", 0)
            dx = mx - pos["x"]
            dy = my - pos["y"]

            if in_strike_zone(dx, dy, facing, atk_range):
                forward, lateral = decompose_offset(dx, dy, facing)
                msg = f"Target {m.get('id')} in strike zone (range {atk_range})! Executing slash attack."
                meta = {
                    "phase": "ATTACK",
                    "target_id": m.get("id"),
                    # Forward depth, not Manhattan sum: it is what the range
                    # bound is actually compared against.
                    "distance": forward,
                    "lateral": lateral,
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
            if dx > 0 and "east" in legal:
                moves.append("east")
            elif dx < 0 and "west" in legal:
                moves.append("west")
            if dy > 0 and "south" in legal:
                moves.append("south")
            elif dy < 0 and "north" in legal:
                moves.append("north")
        else:
            if dy > 0 and "south" in legal:
                moves.append("south")
            elif dy < 0 and "north" in legal:
                moves.append("north")
            if dx > 0 and "east" in legal:
                moves.append("east")
            elif dx < 0 and "west" in legal:
                moves.append("west")

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

    # 4. Roaming exploration
    movement_choices = [a for a in legal if a in ["north", "south", "east", "west", "stay"]]
    choice = random.choice(movement_choices) if movement_choices else "stay"
    meta = {
        "phase": "ROAMING",
        "target_id": None,
        "distance": None,
    }
    return choice, "Exploring territory", meta


def compute_resulting_posture(pos: Dict[str, Any], facing: str, action: str) -> Dict[str, Any]:
    """Calculates the expected position and facing resulting from an action."""
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


# Lateral half-width of the cat's strike zone. Mirrors World.SLASH_HALF_WIDTH.
# tests/test_attack_zone.py asserts the two agree for every facing and range.
SLASH_HALF_WIDTH = 1

# Unit vectors: (forward_dx, forward_dy, lateral_dx, lateral_dy) per facing.
_FACING_AXES = {
    "north": (0, -1, 1, 0),
    "south": (0, 1, -1, 0),
    "east": (1, 0, 0, 1),
    "west": (-1, 0, 0, -1),
}


def decompose_offset(dx: int, dy: int, facing: str):
    """Project a grid offset onto the cat's own axes.

    Returns ``(forward, lateral)``: how many tiles ahead the target is, and how
    far off the centre line. Both are signed; ``forward`` is negative when the
    target is behind.
    """
    fdx, fdy, ldx, ldy = _FACING_AXES.get(facing, _FACING_AXES["north"])
    return dx * fdx + dy * fdy, dx * ldx + dy * ldy


def in_strike_zone(dx: int, dy: int, facing: str, atk_range: int) -> bool:
    """Exact mirror of World.get_slash_aoe_tiles membership.

    The previous reflex used ``abs(dx) + abs(dy) <= atk_range``. Manhattan
    distance is not the shape of a rectangle: a mouse 2 tiles ahead and 1 tile
    off-axis sums to 3, so at range 2 the reflex declined to swing at a tile
    the engine would have hit.
    """
    forward, lateral = decompose_offset(dx, dy, facing)
    return 1 <= forward <= atk_range and abs(lateral) <= SLASH_HALF_WIDTH


def compute_reflex_decision(
    obs: Observation,
    locked_target_id: Optional[str] = None,
    strategy: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Fast reflex decision (System 1) executed at sub-millisecond speeds:
      1. Immediate slash attack if any mouse is in the forward strike zone.
      2. Gap-closing lunge if directly ahead down a corridor.
      3. Target-Lock pursuit: if locked_target_id is visible, step towards it.
      4. General pursuit: step towards nearest visible mouse.
      5. Roaming sweep: explore legal directions if no prey is visible.
    """
    self_info = obs.self
    pos = self_info.get("position", {"x": 0, "y": 0})
    facing = self_info.get("facing", "north")
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]
    mice_seen = [e for e in obs.visible_entities if e.get("type") == "mouse"]

    # 1. Immediate slash attack (highest reflex priority)
    atk_range = (obs.capabilities or {}).get("attack_range", 2)
    if "attack" in legal and mice_seen:
        for m in mice_seen:
            m_pos = m.get("position", {})
            mx, my = m_pos.get("x", 0), m_pos.get("y", 0)
            dx = mx - pos["x"]
            dy = my - pos["y"]

            if in_strike_zone(dx, dy, facing, atk_range):
                forward, lateral = decompose_offset(dx, dy, facing)
                msg = (
                    f"[Reflex Trigger] Prey {m.get('id')} in strike zone "
                    f"({forward} ahead, {lateral:+d} off-axis, range {atk_range}). Attacking."
                )
                meta = {
                    "phase": "ATTACK",
                    "strategy": "ATTACK",
                    "target_id": m.get("id"),
                    "target_mouse_id": m.get("id"),
                    "distance": forward,
                    "target_pos": {"x": mx, "y": my},
                }
                return "attack", msg, meta

    # 2. Forward lunge gap closer
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
                msg = f"[Reflex Trigger] Gap-closing lunge on {m.get('id')}!"
                meta = {
                    "phase": "LUNGE",
                    "strategy": "LUNGE",
                    "target_id": m.get("id"),
                    "target_mouse_id": m.get("id"),
                    "distance": dist,
                    "target_pos": {"x": mx, "y": my},
                }
                return "lunge", msg, meta

    # 3. Target-Lock pursuit (RuneScape style locked follow)
    if locked_target_id and mice_seen:
        matching = [m for m in mice_seen if m.get("id") == locked_target_id]
        if matching:
            target = matching[0]
            tx = target["position"]["x"]
            ty = target["position"]["y"]
            dx = tx - pos["x"]
            dy = ty - pos["y"]
            dist = abs(dx) + abs(dy)

            moves = []
            if abs(dx) >= abs(dy):
                if dx > 0 and "east" in legal:
                    moves.append("east")
                elif dx < 0 and "west" in legal:
                    moves.append("west")
                if dy > 0 and "south" in legal:
                    moves.append("south")
                elif dy < 0 and "north" in legal:
                    moves.append("north")
            else:
                if dy > 0 and "south" in legal:
                    moves.append("south")
                elif dy < 0 and "north" in legal:
                    moves.append("north")
                if dx > 0 and "east" in legal:
                    moves.append("east")
                elif dx < 0 and "west" in legal:
                    moves.append("west")

            if moves:
                strat = strategy or "PURSUIT"
                msg = f"[Target Lock: {locked_target_id}] Advancing on target ({strat}) while macro-planning."
                meta = {
                    "phase": strat,
                    "strategy": strat,
                    "target_id": locked_target_id,
                    "target_mouse_id": locked_target_id,
                    "distance": dist,
                    "target_pos": {"x": tx, "y": ty},
                }
                return moves[0], msg, meta

    # 4. Standard heuristic (pursue nearest visible mouse or roam)
    action, thought, meta = compute_heuristic_decision(obs)
    if strategy and meta.get("phase") == "ROAMING":
        meta["strategy"] = strategy
    return action, thought, meta
