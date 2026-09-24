"""
On-disk runtime store for the Cat actor.

This module is the seam between the three cat processes:

  * ``api.py``      appends one compact record per tick to the transcript.
  * ``subagents/``  reads the whole transcript and writes coaching notes.
  * ``actor.py``    reads the coaching notes into its prompt every request.

Keeping the transcript on disk rather than in RAM means the evaluation window can
be arbitrarily long without growing the agent's memory footprint. The transcript is
scratch data for a single game and is wiped whenever the game resets.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("cat_agent")

def _resolve_runtime_dir(agent_id: Optional[str] = None) -> Path:
    override = os.environ.get("CAT_AGENT_RUNTIME_DIR")
    if override:
        return Path(override)
    target_id = agent_id or os.environ.get("CAT_ID")
    tmp_base = Path(tempfile.gettempdir())
    if not target_id:
        port = os.environ.get("PORT")
        if port == "8003":
            target_id = "cat-2"
        elif port == "8001":
            target_id = "cat-1"
        else:
            return tmp_base / "cm_runtime"
    return tmp_base / f"cm_runtime_{target_id}"


RUNTIME_DIR = _resolve_runtime_dir()
TRANSCRIPT_PATH = RUNTIME_DIR / "transcript.jsonl"
COACHING_NOTES_PATH = RUNTIME_DIR / "coaching_notes.md"
NOTICE_BOARD_PATH = RUNTIME_DIR / "notice_board.md"
COACH_MEMORY_PATH = RUNTIME_DIR / "coach_memory.jsonl"
PROMPTS_PATH = RUNTIME_DIR / "prompts.jsonl"
RUN_CONFIG_PATH = RUNTIME_DIR / "run_config.json"


def _ensure_runtime_dir(target_dir: Optional[Path] = None) -> None:
    d = target_dir or RUNTIME_DIR
    d.mkdir(parents=True, exist_ok=True)


def set_agent_id(agent_id: str) -> None:
    """Dynamically rebinds runtime file paths to the specified agent ID if not overridden by CAT_AGENT_RUNTIME_DIR."""
    global RUNTIME_DIR, TRANSCRIPT_PATH, COACHING_NOTES_PATH, NOTICE_BOARD_PATH, COACH_MEMORY_PATH, PROMPTS_PATH, RUN_CONFIG_PATH
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        return  # explicit env override or test fixture takes precedence
    RUNTIME_DIR = Path(tempfile.gettempdir()) / f"cm_runtime_{agent_id}"
    _ensure_runtime_dir()
    TRANSCRIPT_PATH = RUNTIME_DIR / "transcript.jsonl"
    COACHING_NOTES_PATH = RUNTIME_DIR / "coaching_notes.md"
    NOTICE_BOARD_PATH = RUNTIME_DIR / "notice_board.md"
    COACH_MEMORY_PATH = RUNTIME_DIR / "coach_memory.jsonl"
    PROMPTS_PATH = RUNTIME_DIR / "prompts.jsonl"
    RUN_CONFIG_PATH = RUNTIME_DIR / "run_config.json"


def get_cat_paths(agent_id: str) -> Dict[str, Path]:
    """Returns file paths for a specific cat agent."""
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        cat_dir = Path(os.environ["CAT_AGENT_RUNTIME_DIR"])
    else:
        cat_dir = Path(tempfile.gettempdir()) / f"cm_runtime_{agent_id}"
    _ensure_runtime_dir(cat_dir)
    return {
        "runtime_dir": cat_dir,
        "transcript": cat_dir / "transcript.jsonl",
        "coaching_notes": cat_dir / "coaching_notes.md",
        "notice_board": cat_dir / "notice_board.md",
        "coach_memory": cat_dir / "coach_memory.jsonl",
        "prompts": cat_dir / "prompts.jsonl",
        "run_config": cat_dir / "run_config.json",
    }


def _compact_record(exchange: Dict[str, Any]) -> Dict[str, Any]:
    """Reduces a full tick exchange to the fields the evaluator reasons over.

    The live observation payload carries a lot of rendering detail that is noise to
    a long-horizon advisor. Trimming it here keeps a 100+ tick transcript small
    enough to hand to the model in one piece.
    """
    obs = exchange.get("observation") or {}
    resp = exchange.get("response") or {}
    self_state = obs.get("self") or {}
    posture = resp.get("resulting_posture") or {}

    # Post-action posture, which `act_async` now leaves unchanged on locked ticks.
    # It used to project the move regardless, so the recorded trajectory drifted
    # away from where the cat physically was.
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
        # "play" = the engine accepted this action. "cooldown" = the cat was still
        # locked, the action was discarded, and this row is observation only.
        # Without this the evaluator counted rejected commands as real moves.
        "tick_type": exchange.get("tick_type", "play"),
        "executed": exchange.get("executed", True),
        "action_cooldown": exchange.get("action_cooldown", 0),
        "locked_action": exchange.get("locked_action"),
        "visible": visible,
        # Cumulative captures at this tick. The evaluator reads the whole arc, so
        # it can see exactly which attacks converted and which swung at nothing.
        "score": obs.get("score", 0),
        # The live tunables, exactly as the actor received them this tick.
        "capabilities": obs.get("capabilities") or {},
        # Preserved observation payload so the full perceptual ground truth is retained.
        "observation": obs,
    }


def append_tick(exchange: Dict[str, Any]) -> None:
    """Appends one tick to the transcript. Never raises into the game loop."""
    try:
        _ensure_runtime_dir()
        record = _compact_record(exchange)
        with TRANSCRIPT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception as exc:
        # Transcript logging is diagnostic. It must never stall or crash a tick.
        logger.warning("Failed to append tick to transcript: %s", exc)


def reset_transcript() -> None:
    """Wipes the transcript. Called whenever the game resets."""
    try:
        if TRANSCRIPT_PATH.exists():
            TRANSCRIPT_PATH.unlink()
    except Exception as exc:
        logger.warning("Failed to reset transcript: %s", exc)


def read_transcript_records(agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns every tick recorded for the current game, oldest first."""
    target_path = get_cat_paths(agent_id)["transcript"] if agent_id else TRANSCRIPT_PATH
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
                    # A torn final line can happen if we read mid-write; skip it.
                    continue
    except Exception as exc:
        logger.warning("Failed to read transcript: %s", exc)
    return records


def transcript_tick_count() -> int:
    return len(read_transcript_records())


def format_transcript(records: List[Dict[str, Any]]) -> str:
    """Renders transcript records as readable lines for the model.

    Deliberately emits NO absolute tick index.

    The Actor only ever sees a ~25-tick sliding window, so a directive citing
    "tick 26" refers to something it cannot look up; those citations are pure
    noise in its context. Telling the evaluator not to cite ticks helped but did
    not eliminate it -- the model kept reaching for the numbers because every
    line it read was labelled with one. Not showing it an index is deterministic
    where an instruction is merely probable.

    Ordering is preserved by line order, so runs, oscillations and durations
    remain fully analysable ("roughly 40 attacks", "for the last third").
    """
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
        # A "cooldown" row is a tick the cat was locked: it observed the board but
        # the engine discarded whatever it returned. Rendering these the same as
        # real moves made long runs of rejected commands look like the cat
        # stubbornly repeating itself, and the coach then scolded it for a choice
        # it never got to make.
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
    """Records the full model input behind one Gemini decision.

    Keyed by `applied_at_tick`: the tick whose decision this prompt actually
    produced. That is deliberately not always the tick the prompt was built
    from. In the decoupled path a deliberation starts during one tick and is
    consumed a tick or more later, so `built_at_tick` is recorded separately.
    The workbench inspector looks up by the applied tick, because that is the
    row the user clicks in the trajectory.

    Reflex ticks never call Gemini and so have no record here at all; that
    absence is the honest representation and the UI offers no inspect button
    for them.
    """
    try:
        _ensure_runtime_dir()
        with PROMPTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception as exc:
        # Same contract as the transcript: diagnostics never break a tick.
        logger.warning("Failed to append prompt record: %s", exc)


def read_prompt_for_tick(tick: int) -> Optional[Dict[str, Any]]:
    """Returns the prompt behind the decision applied at `tick`, if captured.

    Scans backwards so that if a tick somehow has more than one record the
    most recent wins, matching what the trajectory row ended up showing.
    """
    if not PROMPTS_PATH.exists():
        return None
    try:
        with PROMPTS_PATH.open("r", encoding="utf-8") as fh:
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


def prompt_log_size() -> Dict[str, Any]:
    """Byte size and record count, so the UI can warn before the file gets silly."""
    if not PROMPTS_PATH.exists():
        return {"bytes": 0, "records": 0}
    try:
        size = PROMPTS_PATH.stat().st_size
        with PROMPTS_PATH.open("r", encoding="utf-8") as fh:
            count = sum(1 for ln in fh if ln.strip())
        return {"bytes": size, "records": count}
    except Exception as exc:
        logger.warning("Failed to stat prompt log: %s", exc)
        return {"bytes": 0, "records": 0}


def reset_prompts() -> None:
    """Wipes the prompt log. Called whenever the game resets."""
    try:
        if PROMPTS_PATH.exists():
            PROMPTS_PATH.unlink()
    except Exception as exc:
        logger.warning("Failed to reset prompt log: %s", exc)


def write_run_config(config: Dict[str, Any]) -> None:
    """Records the model settings behind this run, for the game archive.

    Deliberately not cleared by a reset, unlike everything else in here. The
    other files are scoped to one game; this one describes the process, which
    outlives any number of games. Wiping it would leave the next archive unable
    to say what produced it.
    """
    try:
        _ensure_runtime_dir()
        payload = dict(config)
        payload["written_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        RUN_CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write run config: %s", exc)


def write_coaching_notes(notes: str, tick_range: Optional[str] = None, agent_id: Optional[str] = None) -> None:
    """Publishes the evaluator's advice for the actor to pick up next tick."""
    try:
        target_path = get_cat_paths(agent_id)["coaching_notes"] if agent_id else COACHING_NOTES_PATH
        _ensure_runtime_dir(target_path.parent)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        header = f"<!-- generated {stamp}"
        if tick_range:
            header += f" | ticks {tick_range}"
        header += " -->\n"
        target_path.write_text(header + notes.strip() + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write coaching notes: %s", exc)


def read_coaching_notes(agent_id: Optional[str] = None) -> str:
    """Returns the current coaching notes, or an empty string if none exist yet."""
    target_path = get_cat_paths(agent_id)["coaching_notes"] if agent_id else COACHING_NOTES_PATH
    if not target_path.exists():
        return ""
    try:
        text = target_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read coaching notes: %s", exc)
        return ""
    # Strip the provenance comment; the actor only needs the directives.
    lines = [ln for ln in text.splitlines() if not ln.startswith("<!--")]
    return "\n".join(lines).strip()


def reset_coaching_notes(agent_id: Optional[str] = None) -> None:
    """Clears stale advice so a new game does not inherit the last game's orders."""
    try:
        target_path = get_cat_paths(agent_id)["coaching_notes"] if agent_id else COACHING_NOTES_PATH
        if target_path.exists():
            target_path.unlink()
    except Exception as exc:
        logger.warning("Failed to reset coaching notes: %s", exc)


def read_notice_board() -> str:
    """Returns the current global notice board text, or an empty string."""
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
    """Writes global notice board content for all agents."""
    _ensure_runtime_dir()
    try:
        NOTICE_BOARD_PATH.write_text(content.strip(), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write notice board: %s", exc)


def reset_notice_board() -> None:
    """Clears the global notice board on reset."""
    try:
        if NOTICE_BOARD_PATH.exists():
            NOTICE_BOARD_PATH.unlink()
    except Exception as exc:
        logger.warning("Failed to reset notice board: %s", exc)


import uuid


def get_transcript_ticks_snapshot(ticks: List[int], agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves compact transcript records for specific tick numbers.

    Extracts timestamped context, actor positions, facing, chosen action,
    reasoning thought, and visible entities for RAG traversal and memory linkage.
    """
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
    """Records a coach memory or observation for future recall, optionally linked to transcript ticks."""
    target_path = get_cat_paths(agent_id)["coach_memory"] if agent_id else COACH_MEMORY_PATH
    _ensure_runtime_dir(target_path.parent)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    unique_suffix = uuid.uuid4().hex[:8]
    clean_ticks = [int(t) for t in linked_ticks] if linked_ticks else []
    tick_snapshots = get_transcript_ticks_snapshot(clean_ticks, agent_id=agent_id) if clean_ticks else []

    record = {
        "id": f"mem-{int(time.time() * 1000)}-{unique_suffix}",
        "timestamp": now_str,
        "tick": tick,
        "category": category,
        "tags": tags or [],
        "note": note.strip(),
        "linked_ticks": clean_ticks,
        "tick_snapshots": tick_snapshots,
    }

    try:
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception as exc:
        logger.warning("Failed to append coach memory: %s", exc)
    return record


def read_coach_memories(
    limit: Optional[int] = 10,
    category: Optional[str] = None,
    search: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Returns stored coach memories, newest first, with optional filtering."""
    target_path = get_cat_paths(agent_id)["coach_memory"] if agent_id else COACH_MEMORY_PATH
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
    """Updates an existing coach memory record by ID. Returns updated record or None if not found."""
    target_path = get_cat_paths(agent_id)["coach_memory"] if agent_id else COACH_MEMORY_PATH
    if not target_path.exists():
        return None
    records: List[Dict[str, Any]] = []
    updated_record: Optional[Dict[str, Any]] = None
    clean_ticks = [int(t) for t in linked_ticks] if linked_ticks is not None else None

    try:
        with target_path.open("r", encoding="utf-8") as fh:
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
                            rec["tick_snapshots"] = get_transcript_ticks_snapshot(clean_ticks, agent_id=agent_id)
                        rec["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        updated_record = rec
                    records.append(rec)
                except json.JSONDecodeError:
                    continue
        if updated_record is not None:
            with target_path.open("w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return updated_record
    except Exception as exc:
        logger.warning("Failed to update coach memory %s: %s", memory_id, exc)
        return None


def delete_coach_memory(memory_id: str, agent_id: Optional[str] = None) -> bool:
    """Deletes a coach memory record by ID. Returns True if deleted, False otherwise."""
    target_path = get_cat_paths(agent_id)["coach_memory"] if agent_id else COACH_MEMORY_PATH
    if not target_path.exists():
        return False
    records: List[Dict[str, Any]] = []
    found = False
    try:
        with target_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("id") == memory_id:
                        found = True
                        continue
                    records.append(rec)
                except json.JSONDecodeError:
                    continue
        if found:
            with target_path.open("w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return found
    except Exception as exc:
        logger.warning("Failed to delete coach memory %s: %s", memory_id, exc)
        return False


def reset_coach_memories(agent_id: Optional[str] = None) -> None:
    """Wipes the coach memory store."""
    try:
        target_path = get_cat_paths(agent_id)["coach_memory"] if agent_id else COACH_MEMORY_PATH
        if target_path.exists():
            target_path.unlink()
    except Exception as exc:
        logger.warning("Failed to reset coach memory: %s", exc)


