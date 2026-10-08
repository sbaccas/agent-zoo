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

LOCAL_RUNTIME_DIR = Path(__file__).parent / "runtime"


def _ensure_runtime_dir(target_dir: Optional[Path] = None) -> None:
    if target_dir is not None:
        target_dir.mkdir(parents=True, exist_ok=True)
    elif "RUNTIME_DIR" in globals():
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        LOCAL_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def get_cat_paths(agent_id: str) -> Dict[str, Path]:
    """Returns file paths for a specific cat agent."""
    cid = agent_id or "cat-1"
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        cat_dir = Path(os.environ["CAT_AGENT_RUNTIME_DIR"])
        if agent_id and (Path(os.environ["CAT_AGENT_RUNTIME_DIR"]) / agent_id).is_dir():
            cat_dir = Path(os.environ["CAT_AGENT_RUNTIME_DIR"]) / agent_id
    else:
        # Check active workbench temp runtime directory
        tmp_dir = Path(tempfile.gettempdir()) / f"cm_runtime_{cid}"
        # Also check alias between cat-3 and evil-cat
        alt_cid = "evil-cat" if cid == "cat-3" else ("cat-3" if cid in ("evil-cat", "cat-evil") else None)
        alt_tmp_dir = Path(tempfile.gettempdir()) / f"cm_runtime_{alt_cid}" if alt_cid else None

        if tmp_dir.is_dir():
            cat_dir = tmp_dir
        elif alt_tmp_dir and alt_tmp_dir.is_dir():
            cat_dir = alt_tmp_dir
        elif not agent_id or agent_id == "cat-1":
            cat_dir = LOCAL_RUNTIME_DIR
        else:
            cat_dir = Path(__file__).parent / f"runtime_{cid}"
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
    if not agent_id:
        return LOCAL_RUNTIME_DIR
    return get_cat_paths(agent_id)["runtime_dir"]


RUNTIME_DIR = _resolve_runtime_dir()
TRANSCRIPT_PATH = RUNTIME_DIR / "transcript.jsonl"
COACHING_NOTES_PATH = RUNTIME_DIR / "coaching_notes.md"
NOTICE_BOARD_PATH = RUNTIME_DIR / "notice_board.md"
COACH_MEMORY_PATH = RUNTIME_DIR / "coach_memory.jsonl"
PROMPTS_PATH = RUNTIME_DIR / "prompts.jsonl"
COACH_PROMPTS_PATH = RUNTIME_DIR / "coach_prompts.jsonl"
RUN_CONFIG_PATH = RUNTIME_DIR / "run_config.json"


def _mirror_to_local_runtime(filename: str, content: str, mode: str = "w", agent_id: Optional[str] = None) -> None:
    """Mirrors runtime files directly into the local repository directory so they are always visible."""
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        return
    try:
        tmp_target_id = agent_id or os.environ.get("CAT_ID") or "cat-1"
        # Only mirror to LOCAL_RUNTIME_DIR if it's for cat-1 or a shared file like notice_board.md
        if tmp_target_id == "cat-1" or filename == "notice_board.md":
            LOCAL_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            target = LOCAL_RUNTIME_DIR / filename
            if mode == "a":
                with target.open("a", encoding="utf-8") as fh:
                    fh.write(content)
            else:
                target.write_text(content, encoding="utf-8")

        # Also write to temp folder for external tool compatibility
        tmp_dir = Path(tempfile.gettempdir()) / f"cm_runtime_{tmp_target_id}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_target = tmp_dir / filename
        if mode == "a":
            with tmp_target.open("a", encoding="utf-8") as fh:
                fh.write(content)
        else:
            tmp_target.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.debug("Failed mirroring to local runtime: %s", exc)


def set_agent_id(agent_id: str) -> None:
    """Dynamically rebinds runtime file paths to the specified agent ID if not overridden by CAT_AGENT_RUNTIME_DIR."""
    global RUNTIME_DIR, TRANSCRIPT_PATH, COACHING_NOTES_PATH, NOTICE_BOARD_PATH, COACH_MEMORY_PATH, PROMPTS_PATH, COACH_PROMPTS_PATH, RUN_CONFIG_PATH
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        return  # explicit env override or test fixture takes precedence
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
        active_cat = (exchange.get("observation") or {}).get("self", {}).get("id") or os.environ.get("CAT_ID") or "cat-1"
        target_path = get_cat_paths(active_cat)["transcript"]
        _ensure_runtime_dir(target_path.parent)
        record = _compact_record(exchange)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        if not os.environ.get("CAT_AGENT_RUNTIME_DIR") and active_cat == "cat-1" and target_path.resolve() != (LOCAL_RUNTIME_DIR / "transcript.jsonl").resolve():
            _mirror_to_local_runtime("transcript.jsonl", line, mode="a", agent_id="cat-1")
    except Exception as exc:
        # Transcript logging is diagnostic. It must never stall or crash a tick.
        logger.warning("Failed to append tick to transcript: %s", exc)


def reset_transcript(agent_id: Optional[str] = None) -> None:
    """Wipes the transcript. Called whenever the game resets."""
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id)["transcript"])
    else:
        targets.append(TRANSCRIPT_PATH)
        targets.append(LOCAL_RUNTIME_DIR / "transcript.jsonl")
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
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-1"
    target_path = get_cat_paths(active_cat)["transcript"]
    if not target_path.exists():
        fallback = LOCAL_RUNTIME_DIR / "transcript.jsonl"
        if fallback.exists() and active_cat == "cat-1":
            target_path = fallback
        else:
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


def transcript_tick_count(agent_id: Optional[str] = None) -> int:
    return len(read_transcript_records(agent_id=agent_id))


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
        active_cat = record.get("agent_id") or os.environ.get("CAT_ID") or "cat-1"
        paths = get_cat_paths(active_cat)
        target_path = paths["prompts"]
        _ensure_runtime_dir(target_path.parent)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        if not os.environ.get("CAT_AGENT_RUNTIME_DIR") and active_cat == "cat-1" and target_path.resolve() != (LOCAL_RUNTIME_DIR / "prompts.jsonl").resolve():
            _mirror_to_local_runtime("prompts.jsonl", line, mode="a", agent_id="cat-1")
    except Exception as exc:
        # Same contract as the transcript: diagnostics never break a tick.
        logger.warning("Failed to append prompt record: %s", exc)


def read_prompt_for_tick(tick: int, agent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Returns the prompt behind the decision applied at `tick`, if captured.

    Scans backwards so that if a tick somehow has more than one record the
    most recent wins, matching what the trajectory row ended up showing.
    """
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-1"
    target_path = get_cat_paths(active_cat)["prompts"]
    if not target_path.exists():
        fallback = LOCAL_RUNTIME_DIR / "prompts.jsonl"
        if fallback.exists() and active_cat == "cat-1":
            target_path = fallback
        else:
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
    """Byte size and record count, so the UI can warn before the file gets silly."""
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-1"
    target_path = get_cat_paths(active_cat)["prompts"]
    if not target_path.exists():
        fallback = LOCAL_RUNTIME_DIR / "prompts.jsonl"
        if fallback.exists() and active_cat == "cat-1":
            target_path = fallback
        else:
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
    """Wipes the prompt log. Called whenever the game resets."""
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id)["prompts"])
    else:
        targets.append(PROMPTS_PATH)
        targets.append(LOCAL_RUNTIME_DIR / "prompts.jsonl")
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
    """Records the full model input behind one Coach evaluation pass."""
    try:
        active_cat = agent_id or record.get("agent_id") or os.environ.get("CAT_ID") or "cat-1"
        paths = get_cat_paths(active_cat)
        target_path = paths.get("coach_prompts", COACH_PROMPTS_PATH)
        _ensure_runtime_dir(target_path.parent)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        if not os.environ.get("CAT_AGENT_RUNTIME_DIR") and active_cat == "cat-1" and target_path.resolve() != (LOCAL_RUNTIME_DIR / "coach_prompts.jsonl").resolve():
            _mirror_to_local_runtime("coach_prompts.jsonl", line, mode="a", agent_id="cat-1")
    except Exception as exc:
        logger.warning("Failed to append coach prompt record: %s", exc)


def read_coach_prompt_for_tick(tick: Optional[int] = None, agent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Returns the coach prompt behind an evaluation, if captured.

    If `tick` is provided, finds the coach prompt whose built_at_tick <= tick (most recent evaluation up to that tick).
    If `tick` is None, returns the most recent captured coach prompt record.
    """
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-1"
    paths = get_cat_paths(active_cat)
    target_path = paths.get("coach_prompts", COACH_PROMPTS_PATH)
    if not target_path.exists():
        fallback_path = LOCAL_RUNTIME_DIR / "coach_prompts.jsonl"
        if fallback_path.exists():
            target_path = fallback_path
        else:
            return None

    try:
        with target_path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception as exc:
        logger.warning("Failed to read coach prompt log: %s", exc)
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
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
    """Byte size and record count for coach prompts."""
    active_cat = agent_id or os.environ.get("CAT_ID") or "cat-1"
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
    """Wipes the coach prompt log."""
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id).get("coach_prompts", COACH_PROMPTS_PATH))
    else:
        targets.append(COACH_PROMPTS_PATH)
        targets.append(LOCAL_RUNTIME_DIR / "coach_prompts.jsonl")
        for cid in list_known_cat_ids():
            targets.append(get_cat_paths(cid).get("coach_prompts", COACH_PROMPTS_PATH))
    for p in targets:
        try:
            if p and p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset coach prompt log %s: %s", p, exc)


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
        content = json.dumps(payload, indent=2)
        RUN_CONFIG_PATH.write_text(content, encoding="utf-8")
        active_cat = config.get("agent_id") or os.environ.get("CAT_ID") or "cat-1"
        if not os.environ.get("CAT_AGENT_RUNTIME_DIR") and active_cat == "cat-1" and RUN_CONFIG_PATH.resolve() != (LOCAL_RUNTIME_DIR / "run_config.json").resolve():
            _mirror_to_local_runtime("run_config.json", content, mode="w", agent_id="cat-1")
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
        full_content = header + notes.strip() + "\n"
        target_path.write_text(full_content, encoding="utf-8")
        if not os.environ.get("CAT_AGENT_RUNTIME_DIR") and (not agent_id or agent_id == "cat-1") and target_path.resolve() != (LOCAL_RUNTIME_DIR / "coaching_notes.md").resolve():
            _mirror_to_local_runtime("coaching_notes.md", full_content, mode="w", agent_id=agent_id)
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
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id)["coaching_notes"])
    else:
        targets.append(COACHING_NOTES_PATH)
        targets.append(LOCAL_RUNTIME_DIR / "coaching_notes.md")
        for cid in list_known_cat_ids():
            targets.append(get_cat_paths(cid)["coaching_notes"])
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset coaching notes %s: %s", p, exc)


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
        text = content.strip()
        NOTICE_BOARD_PATH.write_text(text, encoding="utf-8")
        if not os.environ.get("CAT_AGENT_RUNTIME_DIR") and NOTICE_BOARD_PATH.resolve() != (LOCAL_RUNTIME_DIR / "notice_board.md").resolve():
            _mirror_to_local_runtime("notice_board.md", text, mode="w")
    except Exception as exc:
        logger.warning("Failed to write notice board: %s", exc)


def reset_notice_board() -> None:
    """Clears the global notice board on reset."""
    targets = [NOTICE_BOARD_PATH, LOCAL_RUNTIME_DIR / "notice_board.md"]
    for cid in list_known_cat_ids():
        targets.append(get_cat_paths(cid)["notice_board"])
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset notice board %s: %s", p, exc)


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


def list_known_cat_ids() -> List[str]:
    """Discovers available cat agent IDs across active runtime directories."""
    if os.environ.get("CAT_AGENT_RUNTIME_DIR"):
        return [os.environ.get("CAT_ID") or "cat-1"]
    tmp_base = Path(tempfile.gettempdir())
    found: List[str] = []
    for default_id in ["cat-1", "cat-2", "cat-3"]:
        found.append(default_id)
    try:
        for p in tmp_base.glob("cm_runtime_*"):
            if p.is_dir():
                cid = p.name[len("cm_runtime_") :]
                if cid and cid not in ("all", "*") and cid not in found:
                    found.append(cid)
    except Exception:
        pass
    return found


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
    resolved_cat_id = agent_id or os.environ.get("CAT_ID") or "cat-1"

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
        if not os.environ.get("CAT_AGENT_RUNTIME_DIR") and (not agent_id or agent_id == "cat-1") and target_path.resolve() != (LOCAL_RUNTIME_DIR / "coach_memory.jsonl").resolve():
            _mirror_to_local_runtime("coach_memory.jsonl", line, mode="a", agent_id=agent_id)
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
    # When agent_id is "all", or when agent_id is None and default COACH_MEMORY_PATH doesn't exist
    if agent_id in ("all", "*") or (
        agent_id is None and not os.environ.get("CAT_AGENT_RUNTIME_DIR") and not COACH_MEMORY_PATH.exists()
    ):
        records: List[Dict[str, Any]] = []
        seen_ids = set()
        if COACH_MEMORY_PATH.exists():
            try:
                with COACH_MEMORY_PATH.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            if rec.get("id") and rec["id"] not in seen_ids:
                                seen_ids.add(rec["id"])
                                if "cat_id" not in rec:
                                    rec["cat_id"] = "cat-1"
                                records.append(rec)
                        except json.JSONDecodeError:
                            continue
            except Exception as exc:
                logger.warning("Failed to read default coach memory: %s", exc)

        for cid in list_known_cat_ids():
            cpath = get_cat_paths(cid)["coach_memory"]
            if not cpath.exists():
                continue
            try:
                with cpath.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            if rec.get("id") and rec["id"] not in seen_ids:
                                seen_ids.add(rec["id"])
                                if "cat_id" not in rec:
                                    rec["cat_id"] = cid
                                records.append(rec)
                        except json.JSONDecodeError:
                            continue
            except Exception as exc:
                logger.warning("Failed to read coach memory for %s: %s", cid, exc)

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

        def _sort_key(r):
            ts = r.get("timestamp") or ""
            tk = r.get("tick") or 0
            mid = r.get("id") or ""
            return (ts, tk, mid)

        records.sort(key=_sort_key, reverse=True)
        if limit is not None and limit > 0:
            records = records[:limit]
        return records

    target_path = get_cat_paths(agent_id)["coach_memory"] if agent_id else COACH_MEMORY_PATH
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
                        rec["cat_id"] = agent_id or "cat-1"
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
    """Updates an existing coach memory record by ID. Returns updated record or None if not found."""
    clean_ticks = [int(t) for t in linked_ticks] if linked_ticks is not None else None

    def _try_update_in_file(path: Path, cat_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
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
                                rec["tick_snapshots"] = get_transcript_ticks_snapshot(clean_ticks, agent_id=cat_id)
                            rec["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                            if "cat_id" not in rec and cat_id:
                                rec["cat_id"] = cat_id
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
            logger.warning("Failed to update coach memory %s in %s: %s", memory_id, path, exc)
            return None

    if agent_id and agent_id not in ("all", "*"):
        return _try_update_in_file(get_cat_paths(agent_id)["coach_memory"], agent_id)

    res = _try_update_in_file(COACH_MEMORY_PATH, "cat-1")
    if res is not None:
        return res

    for cid in list_known_cat_ids():
        res = _try_update_in_file(get_cat_paths(cid)["coach_memory"], cid)
        if res is not None:
            return res
    return None


def delete_coach_memory(memory_id: str, agent_id: Optional[str] = None) -> bool:
    """Deletes a coach memory record by ID. Returns True if deleted, False otherwise."""
    def _try_delete_in_file(path: Path) -> bool:
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
            logger.warning("Failed to delete coach memory %s in %s: %s", memory_id, path, exc)
            return False

    if agent_id and agent_id not in ("all", "*"):
        return _try_delete_in_file(get_cat_paths(agent_id)["coach_memory"])

    if _try_delete_in_file(COACH_MEMORY_PATH):
        return True

    for cid in list_known_cat_ids():
        if _try_delete_in_file(get_cat_paths(cid)["coach_memory"]):
            return True
    return False


def reset_coach_memories(agent_id: Optional[str] = None) -> None:
    """Wipes the coach memory store."""
    targets = []
    if agent_id and agent_id not in ("all", "*"):
        targets.append(get_cat_paths(agent_id)["coach_memory"])
    else:
        targets.append(COACH_MEMORY_PATH)
        targets.append(LOCAL_RUNTIME_DIR / "coach_memory.jsonl")
        for cid in list_known_cat_ids():
            targets.append(get_cat_paths(cid)["coach_memory"])
    for p in targets:
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning("Failed to reset coach memory %s: %s", p, exc)


