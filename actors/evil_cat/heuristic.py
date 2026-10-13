"""Evil Cat Tactical Heuristic & Fast Reflex (heuristic.py).

Dedicated System-1 reflex and fallback heuristic for Evil Cat (cat-3).
Completely independent of regular cat's mouse-chasing heuristic:
- Evil Cat earns 0 points from mice and absorbs bounty from eliminating rival cats.
- Never falls back to chasing or slashing mice by default.
- Hunts visible live rival cats (prioritized by highest bounty, then proximity),
  or patrols the arena to locate rival cats.
- Honors `locked_target_id` set by Evil Cat's Fast-Brain LLM (including when the
  LLM explicitly targets a mouse in HEAL mode).
"""

import random
from typing import Any, Dict, List, Optional, Tuple
from actors.models import Observation

# Lateral half-width of the cat's strike zone. Mirrors World.SLASH_HALF_WIDTH.
SLASH_HALF_WIDTH = 1

# Unit vectors: (forward_dx, forward_dy, lateral_dx, lateral_dy) per facing.
_FACING_AXES = {
    "north": (0, -1, 1, 0),
    "south": (0, 1, -1, 0),
    "east": (1, 0, 0, 1),
    "west": (-1, 0, 0, -1),
}


def decompose_offset(dx: int, dy: int, facing: str) -> Tuple[int, int]:
    """Project a grid offset onto the cat's own axes.

    Returns ``(forward, lateral)``: how many tiles ahead the target is, and how
    far off the centre line. Both are signed; ``forward`` is negative when the
    target is behind.
    """
    fdx, fdy, ldx, ldy = _FACING_AXES.get(facing, _FACING_AXES["north"])
    return dx * fdx + dy * fdy, dx * ldx + dy * ldy


def in_strike_zone(dx: int, dy: int, facing: str, atk_range: int) -> bool:
    """Exact mirror of World.get_slash_aoe_tiles membership."""
    forward, lateral = decompose_offset(dx, dy, facing)
    return 1 <= forward <= atk_range and abs(lateral) <= SLASH_HALF_WIDTH


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


def _step_moves_toward(
    pos: Dict[str, int], tx: int, ty: int, legal: List[str]
) -> List[str]:
    """Ordered cardinal moves that reduce Manhattan distance to (tx, ty)."""
    dx = tx - pos["x"]
    dy = ty - pos["y"]
    moves: List[str] = []
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

    if not moves:
        cardinal = [a for a in legal if a in ("north", "south", "east", "west")]
        if cardinal:
            def step_dist(action: str) -> int:
                nx, ny = pos["x"], pos["y"]
                if action == "north":
                    ny -= 1
                elif action == "south":
                    ny += 1
                elif action == "east":
                    nx += 1
                elif action == "west":
                    nx -= 1
                return abs(tx - nx) + abs(ty - ny)

            moves = sorted(cardinal, key=step_dist)
    return moves


def _visible_rival_cats(obs: Observation) -> List[Dict[str, Any]]:
    """Returns live rival cat entities visible in FOV, prioritized by bounty then proximity."""
    self_info = obs.self or {}
    self_id = self_info.get("id", "cat-3")
    pos = self_info.get("position", {"x": 0, "y": 0})
    eliminated = set(getattr(obs, "eliminated_cats", []) or [])
    scoreboard = getattr(obs, "scoreboard", {}) or {}

    rivals = [
        e for e in (obs.visible_entities or [])
        if e.get("type") == "cat"
        and e.get("id") != self_id
        and e.get("id") not in eliminated
        and e.get("alive", True) is not False
    ]
    rivals.sort(
        key=lambda c: (
            -int(scoreboard.get(c.get("id", ""), 0) or 0),
            (c.get("position", {}).get("x", 0) - pos["x"]) ** 2
            + (c.get("position", {}).get("y", 0) - pos["y"]) ** 2,
        )
    )
    return rivals


def parse_combat_cat_target(
    directive: str,
    self_id: str = "cat-3",
    eliminated: Optional[List[str]] = None,
) -> Optional[str]:
    """Extracts target cat ID if a directive orders an attack or pursuit on a rival cat."""
    if not directive:
        return None
    d = directive.lower().strip()
    eliminated_set = set(e.lower() for e in (eliminated or []))

    target_id = None
    if "cat-2" in d or "cat 2" in d or "cat2" in d:
        target_id = "cat-2"
    elif "cat-1" in d or "cat 1" in d or "cat1" in d:
        target_id = "cat-1"
    elif any(w in d for w in ("rival", "enemy", "opponent", "other cat", "kill cat", "attack cat")):
        target_id = "cat-1" if "cat-1" not in eliminated_set else "cat-2"

    if not target_id or target_id == self_id or target_id in eliminated_set:
        return None

    combat_keywords = (
        "kill", "attack", "destroy", "eliminate", "hunt", "slay",
        "strike", "slash", "lunge", "chase", "pursue", "take down",
        "take out", "target", "fight", "finish off", "get"
    )
    if any(kw in d for kw in combat_keywords) or target_id:
        return target_id

    return None


def parse_directional_order(directive: str, legal_actions: List[str]) -> Optional[str]:
    """Extracts a movement action if the directive orders a specific direction."""
    if not directive:
        return None
    d = directive.lower().strip()
    for word in ("north", "south", "east", "west", "stay"):
        if word in d and word in legal_actions:
            return word
    return None


def _execute_commander_combat_decision(
    obs: Observation,
    commander_directive: Optional[Dict[str, Any]],
    is_reflex: bool = True,
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """Evaluates whether an active commander directive commands immediate combat against a rival cat or directional move."""
    if not commander_directive:
        return None

    directive_text = (commander_directive.get("directive") or "").strip()
    if not directive_text:
        return None

    self_info = obs.self or {}
    self_id = self_info.get("id", "cat-3")
    pos = self_info.get("position", {"x": 0, "y": 0})
    facing = self_info.get("facing", "north")
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]
    atk_range = (obs.capabilities or {}).get("attack_range", 2)
    eliminated = getattr(obs, "eliminated_cats", []) or []

    target_cat_id = parse_combat_cat_target(directive_text, self_id=self_id, eliminated=eliminated)

    if target_cat_id:
        cat_candidates = [
            e for e in (obs.visible_entities or [])
            if e.get("type") == "cat" and e.get("id") == target_cat_id
        ]
        if not cat_candidates:
            cat_candidates = [
                e for e in (obs.visible_entities or [])
                if e.get("type") == "cat" and e.get("id") != self_id and e.get("id") not in eliminated
            ]

        tag = "Evil Reflex" if is_reflex else "Evil Heuristic"

        if cat_candidates:
            target_cat = cat_candidates[0]
            t_pos = target_cat.get("position", {})
            tx, ty = t_pos.get("x", 0), t_pos.get("y", 0)
            dx = tx - pos["x"]
            dy = ty - pos["y"]

            if "attack" in legal and in_strike_zone(dx, dy, facing, atk_range):
                forward, lateral = decompose_offset(dx, dy, facing)
                msg = (
                    f"[{tag} Priority: Kill {target_cat_id}] Target cat in strike zone "
                    f"({forward} ahead, {lateral:+d} off-axis, range {atk_range}). Attacking!"
                )
                meta = {
                    "phase": "ATTACK",
                    "strategy": "KILL",
                    "target_id": target_cat_id,
                    "target_cat_id": target_cat_id,
                    "distance": forward,
                    "target_pos": {"x": tx, "y": ty},
                }
                return "attack", msg, meta

            if "lunge" in legal:
                directly_ahead = (
                    (facing == "north" and dy < 0 and dx == 0)
                    or (facing == "south" and dy > 0 and dx == 0)
                    or (facing == "east" and dx > 0 and dy == 0)
                    or (facing == "west" and dx < 0 and dy == 0)
                )
                dist = abs(dx) + abs(dy)
                if directly_ahead and dist >= 2:
                    msg = f"[{tag} Priority: Kill {target_cat_id}] Lunging forward to close distance on {target_cat_id}!"
                    meta = {
                        "phase": "LUNGE",
                        "strategy": "KILL",
                        "target_id": target_cat_id,
                        "target_cat_id": target_cat_id,
                        "distance": dist,
                        "target_pos": {"x": tx, "y": ty},
                    }
                    return "lunge", msg, meta

            moves = _step_moves_toward(pos, tx, ty, legal)
            if moves:
                dist = abs(dx) + abs(dy)
                msg = f"[{tag} Priority: Kill {target_cat_id}] Advancing on target cat {target_cat_id} at ({tx}, {ty})."
                meta = {
                    "phase": "PURSUIT",
                    "strategy": "KILL",
                    "target_id": target_cat_id,
                    "target_cat_id": target_cat_id,
                    "distance": dist,
                    "target_pos": {"x": tx, "y": ty},
                }
                return moves[0], msg, meta

        cardinal = [a for a in legal if a in ("north", "south", "east", "west")]
        if cardinal:
            chosen = cardinal[0]
            msg = f"[{tag} Priority: Kill {target_cat_id}] Searching arena to locate target cat {target_cat_id}."
            meta = {
                "phase": "SEARCH",
                "strategy": "STALK",
                "target_id": target_cat_id,
                "target_cat_id": target_cat_id,
            }
            return chosen, msg, meta

    dir_order = parse_directional_order(directive_text, legal)
    if dir_order:
        tag = "Evil Reflex" if is_reflex else "Evil Heuristic"
        msg = f"[{tag} Priority] Executing movement order: {dir_order}."
        meta = {
            "phase": "COMMANDER_MOVE",
            "strategy": "COMMANDER",
            "directive": directive_text,
        }
        return dir_order, msg, meta

    return None


def compute_heuristic_decision(
    obs: Observation,
    commander_directive: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Evaluates an observation for Evil Cat (cat-3) using apex cat-hunter heuristics:
      1. Slash attack if a live rival cat is in the forward strike zone.
      2. High-speed lunge if a live rival cat is aligned directly ahead (distance >= 2).
      3. Stalk / pursue the highest-bounty (or nearest) visible rival cat.
      4. Patrol the arena along cardinal directions when no rival cat is visible.
      Never falls back to chasing mice!
    """
    cmd_override = _execute_commander_combat_decision(obs, commander_directive, is_reflex=False)
    if cmd_override is not None:
        return cmd_override

    self_info = obs.self or {}
    pos = self_info.get("position", {"x": 0, "y": 0})
    facing = self_info.get("facing", "north")
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]
    atk_range = (obs.capabilities or {}).get("attack_range", 2)

    rivals_seen = _visible_rival_cats(obs)

    # 1. Slash attack if a rival cat is in the forward strike zone
    if "attack" in legal and rivals_seen:
        for cat in rivals_seen:
            c_pos = cat.get("position", {})
            cx, cy = c_pos.get("x", 0), c_pos.get("y", 0)
            dx = cx - pos["x"]
            dy = cy - pos["y"]
            if in_strike_zone(dx, dy, facing, atk_range):
                forward, lateral = decompose_offset(dx, dy, facing)
                cid = cat.get("id")
                msg = (
                    f"Rival cat {cid} in strike zone (range {atk_range})! "
                    f"Executing slash attack for bounty."
                )
                meta = {
                    "phase": "ATTACK",
                    "strategy": "KILL",
                    "target_id": cid,
                    "target_cat_id": cid,
                    "distance": forward,
                    "lateral": lateral,
                    "target_pos": {"x": cx, "y": cy},
                }
                return "attack", msg, meta

    # 2. High-speed lunge to close distance on a rival cat aligned ahead
    if "lunge" in legal and rivals_seen:
        for cat in rivals_seen:
            c_pos = cat.get("position", {})
            cx, cy = c_pos.get("x", 0), c_pos.get("y", 0)
            dx = cx - pos["x"]
            dy = cy - pos["y"]
            directly_ahead = (
                (facing == "north" and dy < 0 and dx == 0)
                or (facing == "south" and dy > 0 and dx == 0)
                or (facing == "east" and dx > 0 and dy == 0)
                or (facing == "west" and dx < 0 and dy == 0)
            )
            dist = abs(dx) + abs(dy)
            if directly_ahead and dist >= 2:
                cid = cat.get("id")
                msg = f"Lunging 2 squares forward to close distance on rival cat {cid}!"
                meta = {
                    "phase": "LUNGE",
                    "strategy": "KILL",
                    "target_id": cid,
                    "target_cat_id": cid,
                    "distance": dist,
                    "target_pos": {"x": cx, "y": cy},
                }
                return "lunge", msg, meta

    # 3. Stalk / pursue visible rival cat
    if rivals_seen:
        target = rivals_seen[0]
        t_pos = target.get("position", {})
        tx, ty = t_pos.get("x", 0), t_pos.get("y", 0)
        dist = abs(tx - pos["x"]) + abs(ty - pos["y"])
        moves = _step_moves_toward(pos, tx, ty, legal)
        if moves:
            choice = moves[0]
            cid = target.get("id")
            msg = f"Hunting rival cat {cid} at ({tx}, {ty})"
            meta = {
                "phase": "STALKING",
                "strategy": "STALK",
                "target_id": cid,
                "target_cat_id": cid,
                "distance": dist,
                "target_pos": {"x": tx, "y": ty},
            }
            return choice, msg, meta

    # 4. Patrol arena for rival cats (never chase mice; prefer active cardinal movement)
    cardinal_choices = [a for a in legal if a in ("north", "south", "east", "west")]
    if facing in cardinal_choices and (len(cardinal_choices) == 1 or random.random() < 0.75):
        choice = facing
    elif cardinal_choices:
        choice = random.choice(cardinal_choices)
    else:
        choice = "stay"

    meta = {
        "phase": "ROAMING",
        "strategy": "STALK",
        "target_id": None,
        "distance": None,
    }
    return choice, "Patrolling arena for rival cats", meta


def compute_reflex_decision(
    obs: Observation,
    locked_target_id: Optional[str] = None,
    strategy: Optional[str] = None,
    commander_directive: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Fast reflex decision (System 1) for Evil Cat (cat-3):
      0. Immediate Commander Directive priority override.
      1. Immediate slash attack if any visible rival cat is in the forward strike zone.
      2. Gap-closing lunge if a visible rival cat is aligned ahead down a corridor.
      3. Target-Lock pursuit: if locked_target_id is set by the LLM (rival cat, or
         mouse only when explicitly locked for HEAL), engage or step towards it.
      4. Evil Cat heuristic fallback (stalk visible rival cat or patrol; never chase mice).
    """
    cmd_override = _execute_commander_combat_decision(obs, commander_directive, is_reflex=True)
    if cmd_override is not None:
        return cmd_override

    self_info = obs.self or {}
    self_id = self_info.get("id", "cat-3")
    pos = self_info.get("position", {"x": 0, "y": 0})
    facing = self_info.get("facing", "north")
    legal = obs.legal_actions or ["stay", "north", "south", "east", "west"]
    atk_range = (obs.capabilities or {}).get("attack_range", 2)
    eliminated = set(getattr(obs, "eliminated_cats", []) or [])

    rivals_seen = _visible_rival_cats(obs)

    # 1. Immediate slash attack on any rival cat in the strike zone
    if "attack" in legal and rivals_seen:
        for cat in rivals_seen:
            c_pos = cat.get("position", {})
            cx, cy = c_pos.get("x", 0), c_pos.get("y", 0)
            dx = cx - pos["x"]
            dy = cy - pos["y"]
            if in_strike_zone(dx, dy, facing, atk_range):
                forward, lateral = decompose_offset(dx, dy, facing)
                cid = cat.get("id")
                msg = (
                    f"[Evil Reflex] Rival cat {cid} in strike zone "
                    f"({forward} ahead, {lateral:+d} off-axis, range {atk_range}). Attacking!"
                )
                meta = {
                    "phase": "ATTACK",
                    "strategy": strategy or "KILL",
                    "target_id": cid,
                    "target_cat_id": cid,
                    "distance": forward,
                    "target_pos": {"x": cx, "y": cy},
                }
                return "attack", msg, meta

    # 2. Gap-closing lunge on rival cat aligned ahead
    if "lunge" in legal and rivals_seen:
        for cat in rivals_seen:
            c_pos = cat.get("position", {})
            cx, cy = c_pos.get("x", 0), c_pos.get("y", 0)
            dx = cx - pos["x"]
            dy = cy - pos["y"]
            directly_ahead = (
                (facing == "north" and dy < 0 and dx == 0)
                or (facing == "south" and dy > 0 and dx == 0)
                or (facing == "east" and dx > 0 and dy == 0)
                or (facing == "west" and dx < 0 and dy == 0)
            )
            dist = abs(dx) + abs(dy)
            if directly_ahead and dist >= 2:
                cid = cat.get("id")
                msg = f"[Evil Reflex] Gap-closing lunge on rival cat {cid}!"
                meta = {
                    "phase": "LUNGE",
                    "strategy": strategy or "KILL",
                    "target_id": cid,
                    "target_cat_id": cid,
                    "distance": dist,
                    "target_pos": {"x": cx, "y": cy},
                }
                return "lunge", msg, meta

    # 3. Target-Lock pursuit: honor LLM's locked_target_id (rival cat, or mouse only if LLM locked it)
    if locked_target_id and locked_target_id != self_id and locked_target_id not in eliminated:
        matching = [
            e for e in (obs.visible_entities or [])
            if e.get("id") == locked_target_id
        ]
        if matching:
            target = matching[0]
            t_pos = target.get("position", {})
            tx, ty = t_pos.get("x", 0), t_pos.get("y", 0)
            dx = tx - pos["x"]
            dy = ty - pos["y"]
            dist = abs(dx) + abs(dy)
            strat = strategy or ("HEAL" if target.get("type") == "mouse" else "KILL")

            if "attack" in legal and in_strike_zone(dx, dy, facing, atk_range):
                forward, lateral = decompose_offset(dx, dy, facing)
                msg = (
                    f"[Target Lock: {locked_target_id}] Locked target in strike zone "
                    f"({forward} ahead, {lateral:+d} off-axis). Attacking ({strat})."
                )
                meta = {
                    "phase": "ATTACK",
                    "strategy": strat,
                    "target_id": locked_target_id,
                    "distance": forward,
                    "target_pos": {"x": tx, "y": ty},
                }
                return "attack", msg, meta

            moves = _step_moves_toward(pos, tx, ty, legal)
            if moves:
                msg = f"[Target Lock: {locked_target_id}] Advancing on locked target ({strat}) while macro-planning."
                meta = {
                    "phase": strat,
                    "strategy": strat,
                    "target_id": locked_target_id,
                    "distance": dist,
                    "target_pos": {"x": tx, "y": ty},
                }
                return moves[0], msg, meta

    # 4. Evil Cat heuristic fallback (stalk visible rival cat or patrol; never chase mice)
    action, thought, meta = compute_heuristic_decision(obs)
    if strategy and meta.get("phase") == "ROAMING":
        meta["strategy"] = strategy
    return action, thought, meta
