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
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("cat_agent")

# Overridable so that a test run cannot share these files with a live workbench
# server. Both resolve the same module-level constant, so without an override a
# `pytest` run truncates the running agent's transcript, and the running agent
# appends records into the middle of a test. Tests point this at a temp dir.
RUNTIME_DIR = Path(os.environ.get("CAT_AGENT_RUNTIME_DIR") or (Path(__file__).parent / "runtime"))
TRANSCRIPT_PATH = RUNTIME_DIR / "transcript.jsonl"
COACHING_NOTES_PATH = RUNTIME_DIR / "coaching_notes.md"
COACH_MEMORY_PATH = RUNTIME_DIR / "coach_memory.jsonl"
# Full model inputs, one record per Gemini call, for the workbench's inspector.
PROMPTS_PATH = RUNTIME_DIR / "prompts.jsonl"
# The model settings in force for this run. Written once at startup and read by
# nothing at runtime: it exists so that the game archive can say which
# configuration produced a given box score. Comparing two runs is meaningless
# without it, because the settings that matter most -- the thinking budget, the
# model name -- live in Python and leave no trace in the transcript.
RUN_CONFIG_PATH = RUNTIME_DIR / "run_config.json"


def _ensure_runtime_dir() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


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
        #
        # These are dropped from nothing else and cost ~6 ints per line, but
        # without them the evaluator was advising blind: actor_skills.md states
        # that every range and cooldown printed in it is an illustration and the
        # observation is the only authority, and the evaluator had no
        # observation. So it guessed, told the cat its reach was 3 when the
        # engine said 2, and the cat believed it. They are also per-tick rather
        # than captured once because the operator can retune mid-game.
        "capabilities": obs.get("capabilities") or {},
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


def read_transcript_records() -> List[Dict[str, Any]]:
    """Returns every tick recorded for the current game, oldest first."""
    if not TRANSCRIPT_PATH.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with TRANSCRIPT_PATH.open("r", encoding="utf-8") as fh:
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


def write_coaching_notes(notes: str, tick_range: Optional[str] = None) -> None:
    """Publishes the evaluator's advice for the actor to pick up next tick."""
    try:
        _ensure_runtime_dir()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        header = f"<!-- generated {stamp}"
        if tick_range:
            header += f" | ticks {tick_range}"
        header += " -->\n"
        COACHING_NOTES_PATH.write_text(header + notes.strip() + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write coaching notes: %s", exc)


def read_coaching_notes() -> str:
    """Returns the current coaching notes, or an empty string if none exist yet."""
    if not COACHING_NOTES_PATH.exists():
        return ""
    try:
        text = COACHING_NOTES_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read coaching notes: %s", exc)
        return ""
    # Strip the provenance comment; the actor only needs the directives.
    lines = [ln for ln in text.splitlines() if not ln.startswith("<!--")]
    return "\n".join(lines).strip()


def reset_coaching_notes() -> None:
    """Clears stale advice so a new game does not inherit the last game's orders."""
    try:
        if COACHING_NOTES_PATH.exists():
            COACHING_NOTES_PATH.unlink()
    except Exception as exc:
        logger.warning("Failed to reset coaching notes: %s", exc)


import uuid


def append_coach_memory(
    note: str,
    category: str = "tactical",
    tags: Optional[List[str]] = None,
    tick: Optional[int] = None,
) -> Dict[str, Any]:
    """Records a coach memory or observation for future recall."""
    _ensure_runtime_dir()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    unique_suffix = uuid.uuid4().hex[:8]
    record = {
        "id": f"mem-{int(time.time() * 1000)}-{unique_suffix}",
        "timestamp": now_str,
        "tick": tick,
        "category": category,
        "tags": tags or [],
        "note": note.strip(),
    }

    try:
        with COACH_MEMORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception as exc:
        logger.warning("Failed to append coach memory: %s", exc)
    return record


def read_coach_memories(
    limit: Optional[int] = 10,
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Returns stored coach memories, newest first, with optional filtering."""
    if not COACH_MEMORY_PATH.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with COACH_MEMORY_PATH.open("r", encoding="utf-8") as fh:
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
) -> Optional[Dict[str, Any]]:
    """Updates an existing coach memory record by ID. Returns updated record or None if not found."""
    if not COACH_MEMORY_PATH.exists():
        return None
    records: List[Dict[str, Any]] = []
    updated_record: Optional[Dict[str, Any]] = None
    try:
        with COACH_MEMORY_PATH.open("r", encoding="utf-8") as fh:
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
                        rec["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        updated_record = rec
                    records.append(rec)
                except json.JSONDecodeError:
                    continue
        if updated_record is not None:
            with COACH_MEMORY_PATH.open("w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return updated_record
    except Exception as exc:
        logger.warning("Failed to update coach memory %s: %s", memory_id, exc)
        return None


def delete_coach_memory(memory_id: str) -> bool:
    """Deletes a coach memory record by ID. Returns True if deleted, False otherwise."""
    if not COACH_MEMORY_PATH.exists():
        return False
    records: List[Dict[str, Any]] = []
    found = False
    try:
        with COACH_MEMORY_PATH.open("r", encoding="utf-8") as fh:
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
            with COACH_MEMORY_PATH.open("w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return found
    except Exception as exc:
        logger.warning("Failed to delete coach memory %s: %s", memory_id, exc)
        return False


def reset_coach_memories() -> None:
    """Wipes the coach memory store."""
    try:
        if COACH_MEMORY_PATH.exists():
            COACH_MEMORY_PATH.unlink()
    except Exception as exc:
        logger.warning("Failed to reset coach memory: %s", exc)


