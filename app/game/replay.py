"""Replay timeline builder for archived simulation runs.

Parses archived transcripts across all agents, simulation events, coach
memories, and coaching notes into a unified, tick-indexed replay package with:
- Synchronized positions, facings, actions, HP, and cooldowns for all agents
- Per-tick agent thoughts / decision rationale
- Per-tick perception / visible entities (witness view)
- Cat elimination & death bookmarks with killer, victim, and witness perspectives
- Active coach advice and tactical hypotheses per tick
"""

import gzip
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.game.archive import archive_root

logger = logging.getLogger("cm.replay")

# In-memory cache for parsed replay packages keyed by (archive_id, mtime)
_REPLAY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _read_jsonl_lines(path: Path) -> List[Dict[str, Any]]:
    """Reads a JSONL file, transparently supporting gzip compression and tolerating torn lines."""
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    is_gz = path.name.endswith(".gz")
    try:
        opener = gzip.open(path, "rt", encoding="utf-8") if is_gz else path.open("r", encoding="utf-8")
        with opener as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning("Replay: failed to read %s: %s", path, exc)
    return records


def build_archive_replay(
    archive_id: str,
    from_tick: Optional[int] = None,
    to_tick: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Builds a comprehensive replay package from an archive directory."""
    root = archive_root()
    dest = (root / archive_id).resolve()
    if not dest.is_relative_to(root.resolve()) or not dest.is_dir() or dest.is_symlink():
        return None

    # Check cache based on directory mtime
    dir_mtime = dest.stat().st_mtime
    cached_entry = _REPLAY_CACHE.get(archive_id)
    if cached_entry and cached_entry[0] == dir_mtime and from_tick is None and to_tick is None:
        return cached_entry[1]

    # 1. Load manifest
    manifest: Dict[str, Any] = {}
    manifest_path = dest / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 2. Load boxscore if available
    boxscore: Dict[str, Any] = {}
    boxscore_path = dest / "boxscore.json"
    if boxscore_path.exists():
        try:
            boxscore = json.loads(boxscore_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 3. Load authoritative events.json if available
    authoritative_events: List[Dict[str, Any]] = []
    events_path = dest / "events.json"
    if events_path.exists():
        try:
            authoritative_events = json.loads(events_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Arena metadata
    arena_meta = manifest.get("arena") or {}
    arena_width = int(arena_meta.get("width") or 100)
    arena_height = int(arena_meta.get("height") or 100)
    final_tick = int((manifest.get("final") or {}).get("tick") or 0)

    # 4. Discover all agent directories and known cats across manifest & boxscore
    agent_dirs = sorted(
        [p for p in dest.iterdir() if p.is_dir() and not p.is_symlink() and p.name.startswith("cat-")],
        key=lambda p: (len(p.name), p.name),
    )
    # If no agent subdirectories, check if root itself contains cat-1 transcript
    agent_ids: List[str] = [d.name for d in agent_dirs]
    if not agent_ids:
        if (dest / "transcript.jsonl").exists() or (dest / "transcript.jsonl.gz").exists():
            agent_ids = ["cat-1"]

    known_cats: Set[str] = set(agent_ids)
    if "cats" in boxscore:
        for c in boxscore["cats"]:
            if c.get("id"):
                known_cats.add(str(c["id"]))
    if "scores" in (manifest.get("final") or {}):
        for cid in manifest["final"]["scores"].keys():
            if str(cid).startswith("cat-"):
                known_cats.add(str(cid))
    if "agents" in manifest and isinstance(manifest["agents"], dict):
        for cid in manifest["agents"].keys():
            if str(cid).startswith("cat-"):
                known_cats.add(str(cid))
    all_cats_list = sorted(list(known_cats), key=lambda x: (len(x), x))

    # Authoritative history snapshots if available
    history_snapshots: List[Dict[str, Any]] = []
    hist_path = dest / "history.json"
    if not hist_path.exists():
        hist_path = dest / "history.json.gz"
    if hist_path.exists():
        try:
            if hist_path.name.endswith(".gz"):
                with gzip.open(hist_path, "rt", encoding="utf-8") as fh:
                    history_snapshots = json.load(fh)
            else:
                history_snapshots = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Replay: failed to load %s: %s", hist_path, exc)

    # 5. Extract transcripts, coach memories, and coaching notes per agent
    agent_transcripts: Dict[str, List[Dict[str, Any]]] = {}
    agent_memories: Dict[str, List[Dict[str, Any]]] = {}
    agent_notes: Dict[str, str] = {}

    for cat_id in agent_ids:
        cat_dir = dest / cat_id if (dest / cat_id).is_dir() else dest

        # Transcript
        t_path = cat_dir / "transcript.jsonl"
        if not t_path.exists():
            t_path = cat_dir / "transcript.jsonl.gz"
        agent_transcripts[cat_id] = _read_jsonl_lines(t_path)

        # Coach memories
        m_path = cat_dir / "coach_memory.jsonl"
        agent_memories[cat_id] = _read_jsonl_lines(m_path)

        # Coaching notes
        n_path = cat_dir / "coaching_notes.md"
        if n_path.exists():
            try:
                agent_notes[cat_id] = n_path.read_text(encoding="utf-8")
            except Exception:
                agent_notes[cat_id] = ""

    # Also check root coaching_notes and coach_memory if not loaded
    root_mem_path = dest / "coach_memory.jsonl"
    if root_mem_path.exists() and "cat-1" in agent_ids and not agent_memories.get("cat-1"):
        agent_memories["cat-1"] = _read_jsonl_lines(root_mem_path)

    # 6. Build synchronized frames timeline indexed by tick
    frames_by_tick: Dict[int, Dict[str, Any]] = {}
    max_observed_tick = final_tick

    # Track eliminations per tick
    eliminated_at_tick: Dict[str, int] = {}

    # Initialize from history snapshots if present
    if history_snapshots:
        for snap in history_snapshots:
            t = int(snap.get("tick") or 0)
            if t <= 0:
                continue
            if t > max_observed_tick:
                max_observed_tick = t
            if t not in frames_by_tick:
                frames_by_tick[t] = {
                    "tick": t,
                    "cats": {},
                    "mice": {},
                    "events": [],
                }
            for ent in snap.get("entities") or []:
                etype = str(ent.get("type") or "").lower()
                eid = ent.get("id")
                if not eid:
                    continue
                if etype == "cat":
                    frames_by_tick[t]["cats"][eid] = {
                        "id": eid,
                        "x": ent.get("x"),
                        "y": ent.get("y"),
                        "facing": ent.get("facing", "north"),
                        "action": "move",
                        "thought": "",
                        "source": "sim_history",
                        "hp": ent.get("hp", 100),
                        "alive": ent.get("alive", True),
                        "locked": False,
                        "action_cooldown": 0,
                        "score": (snap.get("scores") or {}).get(eid, 0),
                        "visible": [],
                    }
                elif etype == "mouse":
                    frames_by_tick[t]["mice"][eid] = {
                        "id": eid,
                        "x": ent.get("x"),
                        "y": ent.get("y"),
                        "facing": ent.get("facing", "north"),
                        "hp": ent.get("hp", 100),
                        "alive": ent.get("alive", True),
                    }

    for cat_id, records in agent_transcripts.items():
        for r in records:
            t = r.get("tick")
            if t is None:
                continue
            t = int(t)
            if t > max_observed_tick:
                max_observed_tick = t

            if t not in frames_by_tick:
                frames_by_tick[t] = {
                    "tick": t,
                    "cats": {},
                    "mice": {},
                    "events": [],
                }

            obs = r.get("observation") or {}
            self_state = obs.get("self") or {}
            actual_cat_id = self_state.get("id") or cat_id

            # Position & Posture
            px = r.get("x")
            py = r.get("y")
            if px is None and "position" in self_state:
                px = self_state["position"].get("x")
                py = self_state["position"].get("y")
            if px is None and "state" in r:
                px = r["state"].get("x")
                py = r["state"].get("y")

            facing = r.get("facing") or self_state.get("facing") or (r.get("state", {}) or {}).get("facing") or "north"
            hp = self_state.get("hp") if self_state.get("hp") is not None else (r.get("state", {}) or {}).get("hp", 100)
            alive = (r.get("state", {}) or {}).get("alive", self_state.get("alive", True))
            if hp <= 0:
                alive = False

            if not alive and actual_cat_id not in eliminated_at_tick:
                eliminated_at_tick[actual_cat_id] = t

            action = r.get("action") or (r.get("response") or {}).get("action")
            thought = (
                r.get("thought")
                or (r.get("response") or {}).get("thought")
                or (r.get("agent_state") or {}).get("thought")
                or r.get("prompt")
                or ""
            )
            source = r.get("source") or (r.get("response") or {}).get("source") or "adk_gemini"
            locked = r.get("locked")
            action_cooldown = r.get("action_cooldown") or self_state.get("action_cooldown", 0)

            visible_entities = obs.get("visible_entities") or r.get("visible") or []

            # Populate mice and other cats seen in this cat's FOV into the frame
            compact_vis = []
            for ent in visible_entities:
                pos = ent.get("position") if isinstance(ent.get("position"), dict) else {}
                ex = ent.get("x") if ent.get("x") is not None else pos.get("x")
                ey = ent.get("y") if ent.get("y") is not None else pos.get("y")
                eid = ent.get("id")
                if not eid or ex is None or ey is None:
                    continue

                etype = str(ent.get("type") or ("cat" if str(eid).startswith("cat-") else "mouse")).lower()
                efacing = ent.get("facing", "north")
                ehp = ent.get("hp", 100)
                ealive = ent.get("alive", True) if ehp > 0 else False

                if etype == "mouse":
                    frames_by_tick[t]["mice"][eid] = {
                        "id": eid,
                        "x": ex,
                        "y": ey,
                        "facing": efacing,
                        "hp": ehp,
                        "alive": ealive,
                    }
                elif etype == "cat":
                    if eid not in frames_by_tick[t]["cats"]:
                        frames_by_tick[t]["cats"][eid] = {
                            "id": eid,
                            "x": ex,
                            "y": ey,
                            "facing": efacing,
                            "action": ent.get("action") or "observed",
                            "thought": "",
                            "source": "observed",
                            "hp": ehp,
                            "alive": ealive,
                            "locked": False,
                            "action_cooldown": ent.get("action_cooldown", 0),
                            "score": 0,
                            "visible": [],
                        }

                compact_vis.append({
                    "id": eid,
                    "type": etype,
                    "x": ex,
                    "y": ey,
                    "facing": efacing,
                    "hp": ehp,
                    "forward": ent.get("forward"),
                    "offaxis": ent.get("offaxis"),
                    "in_strike_zone": ent.get("in_strike_zone", False),
                })

            # Check newly eliminated cats mentioned in obs
            for elim_id in obs.get("eliminated_cats") or []:
                if elim_id not in eliminated_at_tick:
                    eliminated_at_tick[elim_id] = t

            existing_cat = frames_by_tick[t]["cats"].get(actual_cat_id, {})
            frames_by_tick[t]["cats"][actual_cat_id] = {
                "id": actual_cat_id,
                "x": px if px is not None else existing_cat.get("x"),
                "y": py if py is not None else existing_cat.get("y"),
                "facing": facing or existing_cat.get("facing", "north"),
                "action": action or existing_cat.get("action"),
                "thought": thought or existing_cat.get("thought", ""),
                "source": source or existing_cat.get("source", "adk_gemini"),
                "hp": hp if hp is not None else existing_cat.get("hp", 100),
                "alive": alive if alive is not None else existing_cat.get("alive", True),
                "locked": locked,
                "action_cooldown": action_cooldown,
                "score": r.get("score") or obs.get("score") or existing_cat.get("score", 0),
                "visible": compact_vis if compact_vis else existing_cat.get("visible", []),
            }

    # Ingest events to anchor real positions and actions for cats and mice
    for ev in authoritative_events:
        t = int(ev.get("tick") or 0)
        if t <= 0:
            continue
        if t > max_observed_tick:
            max_observed_tick = t
        if t not in frames_by_tick:
            frames_by_tick[t] = {
                "tick": t,
                "cats": {},
                "mice": {},
                "events": [],
            }
        frames_by_tick[t]["events"].append(ev)

        etype = str(ev.get("event_type") or "").lower()
        actor = ev.get("actor_id")
        target = ev.get("target_id")
        pos = ev.get("position") if isinstance(ev.get("position"), dict) else {}
        px = pos.get("x")
        py = pos.get("y")

        if actor and str(actor).startswith("cat-") and px is not None and py is not None:
            c_action = "attack" if etype in ("slash", "attack") else etype
            if actor not in frames_by_tick[t]["cats"]:
                frames_by_tick[t]["cats"][actor] = {
                    "id": actor,
                    "x": px,
                    "y": py,
                    "facing": "north",
                    "action": c_action,
                    "thought": "",
                    "source": "event",
                    "hp": 100,
                    "alive": True,
                    "locked": False,
                    "action_cooldown": 0,
                    "score": 0,
                    "visible": [],
                }
            else:
                frames_by_tick[t]["cats"][actor]["action"] = c_action

        if target and str(target).startswith("cat-") and px is not None and py is not None:
            details = ev.get("details") or {}
            target_hp = details.get("remaining_hp", 100) if etype == "damage" else (0 if etype == "elimination" else 100)
            is_alive = False if etype == "elimination" else True
            if etype == "elimination":
                eliminated_at_tick[target] = t
            if target not in frames_by_tick[t]["cats"]:
                frames_by_tick[t]["cats"][target] = {
                    "id": target,
                    "x": px,
                    "y": py,
                    "facing": "north",
                    "action": "eliminated" if etype == "elimination" else "hit",
                    "thought": "",
                    "source": "event",
                    "hp": target_hp,
                    "alive": is_alive,
                    "locked": False,
                    "action_cooldown": 0,
                    "score": 0,
                    "visible": [],
                }

        if target and str(target).startswith("mouse-") and px is not None and py is not None:
            is_m_alive = False if etype == "elimination" else True
            frames_by_tick[t]["mice"][target] = {
                "id": target,
                "x": px,
                "y": py,
                "facing": "north",
                "hp": 0 if not is_m_alive else 100,
                "alive": is_m_alive,
            }

    min_t = min(frames_by_tick.keys(), default=1)
    max_t = max(final_tick, max_observed_tick, 1)

    # Collect known positions per cat across all frames and sources
    cat_keypoints: Dict[str, Dict[int, Dict[str, Any]]] = {cid: {} for cid in all_cats_list}
    for t in sorted(frames_by_tick.keys()):
        frame = frames_by_tick[t]
        for cid, cinfo in frame["cats"].items():
            if cid in cat_keypoints and cinfo.get("x") is not None and cinfo.get("y") is not None:
                cat_keypoints[cid][t] = dict(cinfo)

    # Provide initial deterministic spawn location if cat had zero observations anywhere
    for idx, cid in enumerate(all_cats_list):
        if not cat_keypoints[cid]:
            spawn_x = int(12 + ((idx * 17) % 76))
            spawn_y = int(12 + ((idx * 23) % 76))
            cat_keypoints[cid][1] = {
                "id": cid,
                "x": spawn_x,
                "y": spawn_y,
                "facing": "north",
                "action": "idle",
                "thought": "",
                "source": "spawn",
                "hp": 100,
                "alive": True,
                "locked": False,
                "action_cooldown": 0,
                "score": 0,
                "visible": [],
            }

    # Ensure every tick in sorted_ticks contains all cats
    sorted_ticks = sorted(frames_by_tick.keys())
    for t in sorted_ticks:
        frame = frames_by_tick[t]

        for cid in all_cats_list:
            if cid in frame["cats"] and frame["cats"][cid].get("x") is not None:
                continue

            kfs = cat_keypoints[cid]
            sorted_k_ticks = sorted(kfs.keys())
            first_k_tick = sorted_k_ticks[0]
            last_k_tick = sorted_k_ticks[-1]

            is_eliminated = cid in eliminated_at_tick and t >= eliminated_at_tick[cid]

            if t <= first_k_tick:
                base = kfs[first_k_tick]
                cur_x, cur_y = base["x"], base["y"]
                facing = base.get("facing", "north")
                hp = base.get("hp", 100)
                alive = not is_eliminated
            elif t >= last_k_tick:
                base = kfs[last_k_tick]
                cur_x, cur_y = base["x"], base["y"]
                facing = base.get("facing", "north")
                hp = 0 if is_eliminated else base.get("hp", 100)
                alive = not is_eliminated
            else:
                # Interpolate between surrounding keyframes
                prev_t = max(kt for kt in sorted_k_ticks if kt <= t)
                next_t = min(kt for kt in sorted_k_ticks if kt > t)
                p1 = kfs[prev_t]
                p2 = kfs[next_t]

                if not p1.get("alive", True) or is_eliminated:
                    cur_x, cur_y = p1["x"], p1["y"]
                    facing = p1.get("facing", "north")
                    hp = 0
                    alive = False
                else:
                    span = max(1, next_t - prev_t)
                    frac = (t - prev_t) / span
                    cur_x = round(p1["x"] + frac * (p2["x"] - p1["x"]), 1)
                    cur_y = round(p1["y"] + frac * (p2["y"] - p1["y"]), 1)
                    dx = p2["x"] - p1["x"]
                    dy = p2["y"] - p1["y"]
                    if abs(dx) > abs(dy):
                        facing = "east" if dx > 0 else "west"
                    elif abs(dy) > 0:
                        facing = "south" if dy > 0 else "north"
                    else:
                        facing = p1.get("facing", "north")
                    hp = p1.get("hp", 100)
                    alive = True

            frame["cats"][cid] = {
                "id": cid,
                "x": cur_x,
                "y": cur_y,
                "facing": facing,
                "action": "eliminated" if not alive else "move",
                "thought": "",
                "source": "interpolated",
                "hp": hp,
                "alive": alive,
                "locked": False,
                "action_cooldown": 0,
                "score": 0,
                "visible": [],
            }

    # Maintain mice across frames until eliminated
    known_mice: Dict[str, Dict[str, Any]] = {}
    mouse_elim_ticks: Dict[str, int] = {}
    for ev in authoritative_events:
        etype = str(ev.get("event_type") or "").lower()
        target = ev.get("target_id")
        if etype == "elimination" and target and str(target).startswith("mouse-"):
            mouse_elim_ticks[str(target)] = int(ev.get("tick") or 0)

    for t in sorted_ticks:
        for mid, minfo in frames_by_tick[t]["mice"].items():
            if mid not in known_mice and minfo.get("x") is not None:
                known_mice[mid] = dict(minfo)

    for t in sorted_ticks:
        frame_mice = frames_by_tick[t]["mice"]
        for mid, minfo in known_mice.items():
            if mid in mouse_elim_ticks and t >= mouse_elim_ticks[mid]:
                continue
            if mid not in frame_mice:
                frame_mice[mid] = {
                    "id": mid,
                    "x": minfo.get("x"),
                    "y": minfo.get("y"),
                    "facing": minfo.get("facing", "north"),
                    "hp": 100,
                    "alive": True,
                }

    # 7. Reconstruct Cat Deaths & Elimination Events with Multi-POV / Witness analysis
    deaths: List[Dict[str, Any]] = []
    processed_victims: Set[str] = set()

    sorted_ticks = sorted(frames_by_tick.keys())

    # If authoritative events exist, parse cat eliminations from them
    for ev in authoritative_events:
        etype = str(ev.get("event_type") or ev.get("type") or "").lower()
        details = ev.get("details") or ev.get("data") or {}
        vtype = str(details.get("victim_type") or ev.get("target_type") or "").lower()
        victim = ev.get("target_id") or details.get("victim_id") or details.get("target") or details.get("target_id")
        killer = ev.get("actor_id") or ev.get("source_id") or details.get("killer_id") or details.get("actor_id")

        is_cat_kill = (
            (etype == "elimination" and vtype == "cat")
            or etype in ("cat_killed", "cat_eliminated")
            or (victim and str(victim).startswith("cat-"))
        )
        if is_cat_kill and etype in ("elimination", "cat_killed", "cat_eliminated"):
            tick_num = int(ev.get("tick") or 0)
            if victim and victim not in processed_victims:
                processed_victims.add(victim)

                # Find witnesses at tick_num or tick_num - 1
                frame_at_death = frames_by_tick.get(tick_num, {})
                prev_frame = frames_by_tick.get(tick_num - 1, {})
                witnesses = []
                witness_thoughts = {}

                combined_cids = set((prev_frame.get("cats") or {}).keys()) | set((frame_at_death.get("cats") or {}).keys())
                for cid in combined_cids:
                    if cid not in (victim, killer):
                        c_prev = (prev_frame.get("cats") or {}).get(cid, {})
                        c_death = (frame_at_death.get("cats") or {}).get(cid, {})
                        vis_ids = [v.get("id") for v in (c_prev.get("visible", []) + c_death.get("visible", []))]
                        if victim in vis_ids or (killer and killer in vis_ids):
                            witnesses.append(cid)
                            witness_thoughts[cid] = c_death.get("thought") or c_prev.get("thought", "")

                v_pos = (
                    ev.get("position")
                    or details.get("position")
                    or ({"x": details["x"], "y": details["y"]} if "x" in details and "y" in details else None)
                    or (frame_at_death.get("cats", {}).get(victim) or prev_frame.get("cats", {}).get(victim) or {})
                )
                k_pos = (
                    details.get("killer_pos")
                    or (frame_at_death.get("cats", {}).get(killer) or prev_frame.get("cats", {}).get(killer) or {})
                )

                k_thought = (
                    (frame_at_death.get("cats", {}).get(killer) or {}).get("thought")
                    or (prev_frame.get("cats", {}).get(killer) or {}).get("thought", "")
                )
                v_thought = (
                    (frame_at_death.get("cats", {}).get(victim) or {}).get("thought")
                    or (prev_frame.get("cats", {}).get(victim) or {}).get("thought", "")
                )

                v_x = v_pos.get("x") if isinstance(v_pos, dict) else getattr(v_pos, "x", None)
                v_y = v_pos.get("y") if isinstance(v_pos, dict) else getattr(v_pos, "y", None)
                k_x = k_pos.get("x") if isinstance(k_pos, dict) else getattr(k_pos, "x", None)
                k_y = k_pos.get("y") if isinstance(k_pos, dict) else getattr(k_pos, "y", None)

                deaths.append({
                    "tick": tick_num,
                    "victim_id": victim,
                    "killer_id": killer or "Rival Cat",
                    "source": details.get("source") or details.get("reason", "strike"),
                    "victim_pos": {"x": v_x, "y": v_y},
                    "killer_pos": {"x": k_x, "y": k_y},
                    "x": v_x,
                    "y": v_y,
                    "witnesses": witnesses,
                    "killer_thought": k_thought,
                    "victim_thought": v_thought,
                    "witness_thoughts": witness_thoughts,
                    "message": ev.get("message") or f"{killer} eliminated rival predator {victim}",
                })

    # If authoritative events didn't catch all eliminations, synthesize from transcripts & eliminated_cats
    for i, t in enumerate(sorted_ticks):
        cur_frame = frames_by_tick[t]
        prev_t = sorted_ticks[i - 1] if i > 0 else t
        prev_frame = frames_by_tick.get(prev_t, {})

        # Check for victim whose HP dropped to 0 or was marked eliminated
        for cat_id, cat_info in cur_frame["cats"].items():
            prev_info = prev_frame.get("cats", {}).get(cat_id, {})
            was_alive = prev_info.get("alive", True) and (prev_info.get("hp", 100) > 0)
            is_dead = (not cat_info.get("alive")) or (cat_info.get("hp", 100) <= 0)

            if was_alive and is_dead and cat_id not in processed_victims:
                processed_victims.add(cat_id)
                victim = cat_id
                vx = prev_info.get("x", cat_info.get("x"))
                vy = prev_info.get("y", cat_info.get("y"))

                killer = None
                for other_id, other_info in prev_frame.get("cats", {}).items():
                    if other_id != victim and other_info.get("action") == "attack":
                        ox = other_info.get("x")
                        oy = other_info.get("y")
                        if ox is not None and vx is not None and abs(ox - vx) <= 4 and abs(oy - vy) <= 4:
                            killer = other_id
                            break

                if not killer:
                    for other_id, other_info in cur_frame.get("cats", {}).items():
                        if other_id != victim and victim in (other_info.get("thought") or "").lower():
                            killer = other_id
                            break

                witnesses = []
                witness_thoughts = {}
                for other_id, other_info in prev_frame.get("cats", {}).items():
                    if other_id not in (victim, killer):
                        vis_ids = [v.get("id") for v in other_info.get("visible", [])]
                        if victim in vis_ids or (killer and killer in vis_ids):
                            witnesses.append(other_id)
                            witness_thoughts[other_id] = other_info.get("thought", "")

                k_pos = prev_frame.get("cats", {}).get(killer) or {}
                deaths.append({
                    "tick": t,
                    "victim_id": victim,
                    "killer_id": killer or "Rival Cat",
                    "source": "strike",
                    "victim_pos": {"x": vx, "y": vy},
                    "killer_pos": {"x": k_pos.get("x"), "y": k_pos.get("y")},
                    "x": vx,
                    "y": vy,
                    "witnesses": witnesses,
                    "killer_thought": (prev_frame.get("cats", {}).get(killer) or {}).get("thought", ""),
                    "victim_thought": prev_info.get("thought", ""),
                    "witness_thoughts": witness_thoughts,
                    "message": f"⚡ [TICK {t}] {killer or 'Rival'} eliminated {victim}!",
                })

    # Catch any remaining eliminations discovered via eliminated_cats
    for victim, death_tick in sorted(eliminated_at_tick.items(), key=lambda x: x[1]):
        if victim not in processed_victims:
            processed_victims.add(victim)
            frame_t = frames_by_tick.get(death_tick, {})
            prev_t = frames_by_tick.get(death_tick - 1, frame_t)
            v_info = prev_t.get("cats", {}).get(victim) or frame_t.get("cats", {}).get(victim) or {}
            vx = v_info.get("x")
            vy = v_info.get("y")

            killer = None
            for other_id, other_info in prev_t.get("cats", {}).items():
                if other_id != victim and other_info.get("action") == "attack":
                    ox = other_info.get("x")
                    oy = other_info.get("y")
                    if ox is not None and vx is not None and abs(ox - vx) <= 4 and abs(oy - vy) <= 4:
                        killer = other_id
                        break
            if not killer:
                for other_id, other_info in frame_t.get("cats", {}).items():
                    if other_id != victim and victim in (other_info.get("thought") or "").lower():
                        killer = other_id
                        break

            witnesses = []
            witness_thoughts = {}
            for other_id, other_info in prev_t.get("cats", {}).items():
                if other_id not in (victim, killer):
                    vis_ids = [v.get("id") for v in other_info.get("visible", [])]
                    if victim in vis_ids or (killer and killer in vis_ids):
                        witnesses.append(other_id)
                        witness_thoughts[other_id] = other_info.get("thought", "")

            k_pos = prev_t.get("cats", {}).get(killer) or {}
            deaths.append({
                "tick": death_tick,
                "victim_id": victim,
                "killer_id": killer or "Rival Cat",
                "source": "strike",
                "victim_pos": {"x": vx, "y": vy},
                "killer_pos": {"x": k_pos.get("x"), "y": k_pos.get("y")},
                "x": vx,
                "y": vy,
                "witnesses": witnesses,
                "killer_thought": (prev_t.get("cats", {}).get(killer) or {}).get("thought", ""),
                "victim_thought": v_info.get("thought", ""),
                "witness_thoughts": witness_thoughts,
                "message": f"⚡ [TICK {death_tick}] {killer or 'Rival'} eliminated {victim}!",
            })

    # Sort deaths by tick
    deaths.sort(key=lambda d: d["tick"])

    # 8. Compile coach notes timeline and coach memories
    coach_timeline: Dict[str, Any] = {
        "notes": agent_notes,
        "memories": agent_memories,
    }

    # Convert frames_by_tick to a list of frames, optionally sliced by [from_tick, to_tick]
    all_ticks = sorted(frames_by_tick.keys())
    selected_ticks = all_ticks
    if from_tick is not None:
        selected_ticks = [t for t in selected_ticks if t >= from_tick]
    if to_tick is not None:
        selected_ticks = [t for t in selected_ticks if t <= to_tick]

    frames_list = []
    for t in selected_ticks:
        frame = frames_by_tick[t]
        frames_list.append({
            "tick": t,
            "cats": frame["cats"],
            "mice": list(frame["mice"].values()),
            "events": frame["events"],
        })

    replay_package = {
        "archive_id": archive_id,
        "arena": {
            "width": arena_width,
            "height": arena_height,
        },
        "agents": manifest.get("agents") or {cid: "auto" for cid in agent_ids},
        "meta": {
            "simulation_id": manifest.get("simulation_id") or archive_id,
            "reason": manifest.get("reason", "unknown"),
            "archived_at": manifest.get("archived_at"),
            "total_ticks": max_observed_tick,
            "min_tick": all_ticks[0] if all_ticks else 1,
            "max_tick": all_ticks[-1] if all_ticks else max_observed_tick,
            "arena": {
                "width": arena_width,
                "height": arena_height,
            },
            "agents": manifest.get("agents") or {cid: "auto" for cid in agent_ids},
            "final_scores": (manifest.get("final") or {}).get("scores") or {},
        },
        "cats": all_cats_list,
        "deaths": deaths,
        "coach": coach_timeline,
        "frames": frames_list,
    }

    if from_tick is None and to_tick is None:
        _REPLAY_CACHE[archive_id] = (dir_mtime, replay_package)

    return replay_package
