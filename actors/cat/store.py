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
from typing import Any, Dict, List, Optional

logger = logging.getLogger("cat_agent")

# Overridable so that a test run cannot share these files with a live workbench
# server. Both resolve the same module-level constant, so without an override a
# `pytest` run truncates the running agent's transcript, and the running agent
# appends records into the middle of a test. Tests point this at a temp dir.
RUNTIME_DIR = Path(os.environ.get("CAT_AGENT_RUNTIME_DIR") or (Path(__file__).parent / "runtime"))
TRANSCRIPT_PATH = RUNTIME_DIR / "transcript.jsonl"
COACHING_NOTES_PATH = RUNTIME_DIR / "coaching_notes.md"


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

    # Prefer the post-action posture so the transcript reads as a true trajectory.
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
        "visible": visible,
        # Cumulative captures at this tick. The evaluator reads the whole arc, so
        # it can see exactly which attacks converted and which swung at nothing.
        "score": obs.get("score", 0),
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
