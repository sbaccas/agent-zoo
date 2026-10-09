"""On-disk runtime store for Evil Cat (cat-3) and Evil Coach.

Completely independent module with zero imports from `actors.cat`.
Persists transcripts, standing directives (`coaching_notes.md`), private coach
memories (`coach_memory.jsonl`), and prompt logs (`prompts.jsonl`, `coach_prompts.jsonl`).
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("evil_cat_store")

LOCAL_RUNTIME_DIR = Path(__file__).parent / "runtime"
SHARED_RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "cat"


def _ensure_runtime_dir(target_dir: Optional[Path] = None) -> None:
    if target_dir is not None:
        target_dir.mkdir(parents=True, exist_ok=True)
    elif "RUNTIME_DIR" in globals():
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        LOCAL_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def get_cat_paths(agent_id: Optional[str] = "cat-3") -> Dict[str, Path]:
    """Returns file paths for Evil Cat (or a specified cat agent ID)."""
    cid = agent_id or os.environ.get("CAT_ID") or "cat-3"
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        base_dir = Path(os.environ["CAT_AGENT_RUNTIME_DIR"])
        if cid and cid != "cat-1" and not base_dir.name.endswith(f"_{cid}"):
            cat_dir = base_dir / cid
        else:
            cat_dir = base_dir
    else:
        tmp_dir = Path(tempfile.gettempdir()) / f"cm_runtime_{cid}"
        alt_cid = "evil-cat" if cid == "cat-3" else ("cat-3" if cid in ("evil-cat", "cat-evil") else None)
        alt_tmp_dir = Path(tempfile.gettempdir()) / f"cm_runtime_{alt_cid}" if alt_cid else None

        if tmp_dir.is_dir():
            cat_dir = tmp_dir
        elif alt_tmp_dir and alt_tmp_dir.is_dir():
            cat_dir = alt_tmp_dir
        else:
            cat_dir = SHARED_RUNTIME_ROOT / f"runtime_{cid}"
    _ensure_runtime_dir(cat_dir)
    return {
        "runtime_dir": cat_dir,
        "transcript": cat_dir / "transcript.jsonl",
        "coaching_notes": cat_dir / "coaching_notes.md",
        "notice_board": cat_dir / "notice_board.md",
        "coach_memory": cat_dir / "coach_memory.jsonl",
        "prompts": cat_dir / "prompts.jsonl",
        "coach_prompts": cat_dir / "coach_prompts.jsonl",
        "run_config": cat_dir / "run_config.json",
    }


def _resolve_runtime_dir(agent_id: Optional[str] = None) -> Path:
    override = os.environ.get("CAT_AGENT_RUNTIME_DIR")
    if override:
        return Path(override)
    return get_cat_paths(agent_id or "cat-3")["runtime_dir"]


RUNTIME_DIR = _resolve_runtime_dir()
TRANSCRIPT_PATH = RUNTIME_DIR / "transcript.jsonl"
COACHING_NOTES_PATH = RUNTIME_DIR / "coaching_notes.md"
NOTICE_BOARD_PATH = RUNTIME_DIR / "notice_board.md"
COACH_MEMORY_PATH = RUNTIME_DIR / "coach_memory.jsonl"
PROMPTS_PATH = RUNTIME_DIR / "prompts.jsonl"
COACH_PROMPTS_PATH = RUNTIME_DIR / "coach_prompts.jsonl"
RUN_CONFIG_PATH = RUNTIME_DIR / "run_config.json"


def set_agent_id(agent_id: str) -> None:
    """Dynamically rebinds runtime file paths to the specified agent ID."""
    global RUNTIME_DIR, TRANSCRIPT_PATH, COACHING_NOTES_PATH, NOTICE_BOARD_PATH, COACH_MEMORY_PATH, PROMPTS_PATH, COACH_PROMPTS_PATH, RUN_CONFIG_PATH
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        return
    paths = get_cat_paths(agent_id)
    RUNTIME_DIR = paths["runtime_dir"]
    _ensure_runtime_dir(RUNTIME_DIR)
    TRANSCRIPT_PATH = paths["transcript"]
    COACHING_NOTES_PATH = paths["coaching_notes"]
    NOTICE_BOARD_PATH = paths["notice_board"]
    COACH_MEMORY_PATH = paths["coach_memory"]
    PROMPTS_PATH = paths["prompts"]
    COACH_PROMPTS_PATH = paths["coach_prompts"]
    RUN_CONFIG_PATH = paths["run_config"]


def list_known_cat_ids() -> List[str]:
    """Discovers available cat agent IDs across active runtime directories."""
    found: List[str] = ["cat-1", "cat-2", "cat-3"]
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        cid = os.environ.get("CAT_ID")
        if cid and cid not in found:
            found.append(cid)
        return found
    tmp_base = Path(tempfile.gettempdir())
    try:
        for p in tmp_base.glob("cm_runtime_*"):
            if p.is_dir():
                cid = p.name[len("cm_runtime_") :]
                if cid and cid not in ("all", "*") and cid not in found:
                    found.append(cid)
    except Exception:
        pass
    return found


def _compact_record(exchange: Dict[str, Any]) -> Dict[str, Any]:
    obs = exchange.get("observation") or {}
    resp = exchange.get("response") or {}
    self_state = obs.get("self") or {}
    posture = resp.get("resulting_posture") or {}

    position = posture.get("position") or self_state.get("position") or {}
    facing = posture.get("facing") or self_state.get("facing") or "north"

    visible = []
    for ent in obs.get("visible_entities") or []:
        pos = ent.get("position") or {}
        visible.append(
            {
                "id": ent.get("id"),
                "type": ent.get("type"),
                "x": pos.get("x"),
                "y": pos.get("y"),
            }
        )

    return {
        "tick": exchange.get("tick"),
        "x": position.get("x"),
        "y": position.get("y"),
        "facing": facing,
        "action": resp.get("action"),
        "thought": resp.get("thought"),
        "source": resp.get("source") or exchange.get("source"),
        "tick_type": exchange.get("tick_type", "play"),
        "executed": exchange.get("executed", True),
        "action_cooldown": exchange.get("action_cooldown", 0),
        "locked_action": exchange.get("locked_action"),
        "visible": visible,
        "score": obs.get("score", 0),
        "capabilities": obs.get("capabilities") or {},
        "observation": obs,
    }


def append_tick(exchange: Dict[str, Any]) -> None:
    """Appends one tick to Evil Cat's transcript."""
    try:
        active_cat = (exchange.get("observation") or {}).get("self", {}).get("id") or os.environ.get("CAT_ID") or "cat-3"
        target_path = get_cat_paths(active_cat)["transcript"]
        _ensure_runtime_dir(target_path.parent)
        record = _compact_record(exchange)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        logger.warning("Failed to append tick to transcript: %s", exc)


def reset_transcript(agent_id: Optional[str] = None) -> None:
    """Wipes the transcript on game reset."""
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id)["transcript"])
    else:
        targets.append(TRANSCRIPT_PATH)
        for cid in list_known_cat_ids():
            targets.append(get_cat_paths(cid)["transcript"])
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset transcript %s: %s", p, exc)


def read_transcript_records(agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns every tick recorded for the current game, oldest first."""
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-3"
    target_path = get_cat_paths(active_cat)["transcript"]
    if not target_path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with target_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning("Failed to read transcript: %s", exc)
    return records


def transcript_tick_count(agent_id: Optional[str] = None) -> int:
    return len(read_transcript_records(agent_id=agent_id))


def format_transcript(records: List[Dict[str, Any]]) -> str:
    """Renders transcript records as readable lines for the Evil Coach."""
    lines = []
    for r in records:
        visible = r.get("visible") or []
        if visible:
            seen = ", ".join(
                f"{v.get('id')}@({v.get('x')},{v.get('y')})" for v in visible
            )
        else:
            seen = "nothing"
        action = (r.get("action") or "stay").upper()
        thought = r.get("thought") or ""
        if r.get("tick_type") == "cooldown" or r.get("executed") is False:
            did = f"LOCKED (recovering from {r.get('locked_action') or 'previous action'}; no action possible)"
        else:
            did = f"did {action}"
        lines.append(
            f"at ({r.get('x')},{r.get('y')}) facing "
            f"{r.get('facing')} | score {r.get('score', 0)} | saw {seen} | {did}"
            + (f' | reasoning: "{thought}"' if thought else "")
        )
    return "\n".join(lines)


def append_prompt(record: Dict[str, Any]) -> None:
    """Records the full model input behind one Gemini decision."""
    try:
        active_cat = record.get("agent_id") or os.environ.get("CAT_ID") or "cat-3"
        target_path = get_cat_paths(active_cat)["prompts"]
        _ensure_runtime_dir(target_path.parent)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        logger.warning("Failed to append prompt record: %s", exc)


def read_prompt_for_tick(tick: int, agent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Returns the prompt behind the decision applied at `tick`, if captured."""
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-3"
    target_path = get_cat_paths(active_cat)["prompts"]
    if not target_path.exists():
        return None
    try:
        with target_path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception as exc:
        logger.warning("Failed to read prompt log: %s", exc)
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("applied_at_tick") == tick:
            return rec
    return None


def prompt_log_size(agent_id: Optional[str] = None) -> Dict[str, Any]:
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-3"
    target_path = get_cat_paths(active_cat)["prompts"]
    if not target_path.exists():
        return {"bytes": 0, "records": 0}
    try:
        size = target_path.stat().st_size
        with target_path.open("r", encoding="utf-8") as fh:
            count = sum(1 for ln in fh if ln.strip())
        return {"bytes": size, "records": count}
    except Exception as exc:
        logger.warning("Failed to stat prompt log: %s", exc)
        return {"bytes": 0, "records": 0}


def reset_prompts(agent_id: Optional[str] = None) -> None:
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id)["prompts"])
    else:
        targets.append(PROMPTS_PATH)
        for cid in list_known_cat_ids():
            targets.append(get_cat_paths(cid)["prompts"])
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset prompt log %s: %s", p, exc)
    reset_coach_prompts(agent_id)


def append_coach_prompt(record: Dict[str, Any], agent_id: Optional[str] = None) -> None:
    """Records the full model input behind one Evil Coach evaluation pass."""
    try:
        active_cat = agent_id or record.get("agent_id") or os.environ.get("CAT_ID") or "cat-3"
        paths = get_cat_paths(active_cat)
        target_path = paths.get("coach_prompts", COACH_PROMPTS_PATH)
        _ensure_runtime_dir(target_path.parent)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        logger.warning("Failed to append coach prompt record: %s", exc)


def read_coach_prompt_for_tick(tick: Optional[int] = None, agent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-3"
    candidate_paths: List[Path] = []
    paths = get_cat_paths(active_cat)
    target_path = paths.get("coach_prompts", COACH_PROMPTS_PATH)
    if target_path.exists():
        candidate_paths.append(target_path)
    for cid in list_known_cat_ids():
        cp = get_cat_paths(cid).get("coach_prompts")
        if cp and cp.exists() and cp not in candidate_paths:
            candidate_paths.append(cp)

    for p in candidate_paths:
        try:
            with p.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except Exception:
            continue

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec_agent = rec.get("agent_id") or rec.get("cat_id")
            if rec_agent and active_cat and rec_agent != active_cat:
                continue
            if tick is None:
                return rec
            built = rec.get("built_at_tick")
            applied = rec.get("applied_at_tick")
            if built == tick or applied == tick:
                return rec
            if built is not None and built <= tick:
                return rec

    return None


def coach_prompt_log_size(agent_id: Optional[str] = None) -> Dict[str, Any]:
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-3"
    paths = get_cat_paths(active_cat)
    target_path = paths.get("coach_prompts", COACH_PROMPTS_PATH)
    if not target_path.exists():
        return {"bytes": 0, "records": 0}
    try:
        size = target_path.stat().st_size
        with target_path.open("r", encoding="utf-8") as fh:
            count = sum(1 for ln in fh if ln.strip())
        return {"bytes": size, "records": count}
    except Exception as exc:
        logger.warning("Failed to stat coach prompt log: %s", exc)
        return {"bytes": 0, "records": 0}


def reset_coach_prompts(agent_id: Optional[str] = None) -> None:
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id).get("coach_prompts", COACH_PROMPTS_PATH))
    else:
        targets.append(COACH_PROMPTS_PATH)
        for cid in list_known_cat_ids():
            targets.append(get_cat_paths(cid).get("coach_prompts", COACH_PROMPTS_PATH))
    for p in targets:
        try:
            if p and p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset coach prompt log %s: %s", p, exc)


def write_run_config(config: Dict[str, Any], agent_id: Optional[str] = None) -> None:
    try:
        active_cat = agent_id or config.get("agent_id") or os.environ.get("CAT_ID") or "cat-3"
        target_path = get_cat_paths(active_cat)["run_config"]
        _ensure_runtime_dir(target_path.parent)
        payload = dict(config)
        payload["written_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        content = json.dumps(payload, indent=2)
        target_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write run config: %s", exc)


def write_coaching_notes(notes: str, tick_range: Optional[str] = None, agent_id: Optional[str] = None) -> None:
    try:
        target_path = get_cat_paths(agent_id or "cat-3")["coaching_notes"]
        _ensure_runtime_dir(target_path.parent)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        header = f"<!-- generated {stamp}"
        if tick_range:
            header += f" | ticks {tick_range}"
        header += " -->\n"
        full_content = header + notes.strip() + "\n"
        target_path.write_text(full_content, encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write coaching notes: %s", exc)


def read_coaching_notes(agent_id: Optional[str] = None) -> str:
    target_path = get_cat_paths(agent_id or "cat-3")["coaching_notes"]
    if not target_path.exists():
        return ""
    try:
        text = target_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read coaching notes: %s", exc)
        return ""
    lines = [ln for ln in text.splitlines() if not ln.startswith("<!--")]
    return "\n".join(lines).strip()


def reset_coaching_notes(agent_id: Optional[str] = None) -> None:
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id)["coaching_notes"])
    else:
        targets.append(COACHING_NOTES_PATH)
        for cid in list_known_cat_ids():
            targets.append(get_cat_paths(cid)["coaching_notes"])
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset coaching notes %s: %s", p, exc)


def read_notice_board() -> str:
    if not NOTICE_BOARD_PATH.exists():
        return ""
    try:
        text = NOTICE_BOARD_PATH.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if not ln.startswith("<!--")]
        return "\n".join(lines).strip()
    except Exception as exc:
        logger.warning("Failed to read notice board: %s", exc)
        return ""


def write_notice_board(content: str) -> None:
    _ensure_runtime_dir()
    try:
        NOTICE_BOARD_PATH.write_text(content.strip(), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write notice board: %s", exc)


def reset_notice_board() -> None:
    targets = [NOTICE_BOARD_PATH]
    for cid in list_known_cat_ids():
        targets.append(get_cat_paths(cid)["notice_board"])
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset notice board %s: %s", p, exc)


def get_transcript_ticks_snapshot(ticks: List[int], agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if not ticks:
        return []
    tick_set = set(ticks)
    records = read_transcript_records(agent_id=agent_id)
    snapshots = []
    for r in records:
        if r.get("tick") in tick_set:
            snapshots.append(
                {
                    "tick": r.get("tick"),
                    "x": r.get("x"),
                    "y": r.get("y"),
                    "facing": r.get("facing"),
                    "action": r.get("action"),
                    "thought": r.get("thought"),
                    "score": r.get("score"),
                    "tick_type": r.get("tick_type"),
                    "visible": r.get("visible"),
                }
            )
    return snapshots


def append_coach_memory(
    note: str,
    category: str = "tactical",
    tags: Optional[List[str]] = None,
    tick: Optional[int] = None,
    linked_ticks: Optional[List[int]] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_cat_id = agent_id or os.environ.get("CAT_ID") or "cat-3"
    target_path = get_cat_paths(resolved_cat_id)["coach_memory"]
    _ensure_runtime_dir(target_path.parent)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    unique_suffix = uuid.uuid4().hex[:8]
    clean_ticks = [int(t) for t in linked_ticks] if linked_ticks else []
    tick_snapshots = get_transcript_ticks_snapshot(clean_ticks, agent_id=resolved_cat_id) if clean_ticks else []

    record = {
        "id": f"mem-{int(time.time() * 1000)}-{unique_suffix}",
        "timestamp": now_str,
        "tick": tick,
        "cat_id": resolved_cat_id,
        "category": category,
        "tags": tags or [],
        "note": note.strip(),
        "linked_ticks": clean_ticks,
        "tick_snapshots": tick_snapshots,
    }

    try:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        logger.warning("Failed to append coach memory: %s", exc)
    return record


def read_coach_memories(
    limit: Optional[int] = 10,
    category: Optional[str] = None,
    search: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    resolved_cat_id = agent_id or os.environ.get("CAT_ID") or "cat-3"
    target_path = get_cat_paths(resolved_cat_id)["coach_memory"]
    if not target_path.exists():
        return []
    records = []
    try:
        with target_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if "cat_id" not in rec:
                        rec["cat_id"] = resolved_cat_id
                    records.append(rec)
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning("Failed to read coach memory: %s", exc)
        return []

    if category:
        records = [r for r in records if r.get("category") == category]
    if search:
        s_lower = search.lower()
        records = [
            r
            for r in records
            if s_lower in r.get("note", "").lower()
            or any(s_lower in str(t).lower() for t in r.get("tags", []))
        ]

    records.reverse()
    if limit is not None and limit > 0:
        records = records[:limit]
    return records


def update_coach_memory(
    memory_id: str,
    note: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[List[str]] = None,
    linked_ticks: Optional[List[int]] = None,
    agent_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_cat_id = agent_id or os.environ.get("CAT_ID") or "cat-3"
    path = get_cat_paths(resolved_cat_id)["coach_memory"]
    if not path.exists():
        return None
    clean_ticks = [int(t) for t in linked_ticks] if linked_ticks is not None else None
    recs: List[Dict[str, Any]] = []
    updated: Optional[Dict[str, Any]] = None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("id") == memory_id:
                        if note is not None:
                            rec["note"] = note.strip()
                        if category is not None:
                            rec["category"] = category
                        if tags is not None:
                            rec["tags"] = tags
                        if clean_ticks is not None:
                            rec["linked_ticks"] = clean_ticks
                            rec["tick_snapshots"] = get_transcript_ticks_snapshot(clean_ticks, agent_id=resolved_cat_id)
                        rec["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        updated = rec
                    recs.append(rec)
                except json.JSONDecodeError:
                    continue
        if updated is not None:
            with path.open("w", encoding="utf-8") as fh:
                for r in recs:
                    fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        return updated
    except Exception as exc:
        logger.warning("Failed to update coach memory %s: %s", memory_id, exc)
        return None


def delete_coach_memory(memory_id: str, agent_id: Optional[str] = None) -> bool:
    resolved_cat_id = agent_id or os.environ.get("CAT_ID") or "cat-3"
    path = get_cat_paths(resolved_cat_id)["coach_memory"]
    if not path.exists():
        return False
    recs: List[Dict[str, Any]] = []
    found = False
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("id") == memory_id:
                        found = True
                        continue
                    recs.append(rec)
                except json.JSONDecodeError:
                    continue
        if found:
            with path.open("w", encoding="utf-8") as fh:
                for r in recs:
                    fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        return found
    except Exception as exc:
        logger.warning("Failed to delete coach memory %s: %s", memory_id, exc)
        return False


def reset_coach_memories(agent_id: Optional[str] = None) -> None:
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id)["coach_memory"])
    else:
        targets.append(COACH_MEMORY_PATH)
        for cid in list_known_cat_ids():
            targets.append(get_cat_paths(cid)["coach_memory"])
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset coach memory %s: %s", p, exc)
