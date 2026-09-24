"""Preserves a finished game on disk so two runs can be compared afterwards.

Why the game server owns this and the cat actor does not
--------------------------------------------------------
The interesting evidence for a run is split across two processes. The game
server (:8000) holds the box score; the cat actor (:8001) holds the transcript,
the prompt log and the coaching notes on disk under ``actors/cat/runtime/``.

Only the game server can archive the box score, because only the game server
knows a reset is happening *before* it happens. ``Simulation.reset()`` calls
``stats.reset()`` synchronously, while the actor discovers the reset later, on
a subsequent poll, by noticing the tick counter has gone backwards. By that
point every counter reads zero. An actor-side archiver would therefore save a
perfect copy of nothing.

So the game server archives both halves: it snapshots its own counters in
memory, then copies the actor's runtime files off disk. Reading those files
across the process boundary is safe. They are append-only, each record is
written with a single ``write`` call, and every reader in the codebase already
tolerates a torn final line (see ``store.read_transcript_records``).

What an archive is for
----------------------
Comparing two runs is only meaningful if you know what differed between them,
so the manifest records the independent variables alongside the outcome: the
arena and population settings, which cat was driven by the model and which by
the heuristic, the commit the server was running, and a byte-for-byte copy of
every markdown file in the actor package -- those files *are* the prompt, and
they change between runs without leaving a trace in git if uncommitted.

Failure policy
--------------
Archiving is bookkeeping. It must never prevent a reset or a shutdown, so every
entry point swallows its exceptions and logs instead.
"""

from datetime import datetime, timezone
import asyncio
import gzip
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("simulation")

REPO_ROOT = Path(__file__).resolve().parents[2]

# Copied rather than compressed below this size. gzip buys little on small
# files and plain files stay greppable, which matters for the ones a human
# actually opens.
GZIP_THRESHOLD_BYTES = 2 * 1024 * 1024

MANIFEST_SCHEMA_VERSION = 1

# Files lifted from the cat actor's runtime directory, in the order they appear
# in the manifest. `compress` is advisory: see GZIP_THRESHOLD_BYTES.
RUNTIME_ARTEFACTS = (
    # The per-tick record of what the cat did. The basis for action-mix stats.
    ("transcript.jsonl", True),
    # Full model inputs, one per Gemini call. By far the largest artefact
    # (~55 MB for a 400-tick game, ~13 MB gzipped) and also the most valuable:
    # it is the only record of what the model actually saw, and it doubles as a
    # replayable corpus for offline prompt experiments.
    ("prompts.jsonl", True),
    # The coaching notes as they stood when the game ended.
    ("coaching_notes.md", False),
    # Notice board bulletin active during this run.
    ("notice_board.md", False),
    # Coach tactical memories and hypotheses log written via MCP / evaluator.
    ("coach_memory.jsonl", False),
    # Written by the actor process, if it writes one: the live model settings
    # behind this run. Absent is recorded as absent, never as empty.
    ("run_config.json", False),
)



def _truthy(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def env_flag(name: str, default: bool) -> bool:
    """Reads a boolean switch from the environment, at call time.

    Call time rather than import time throughout this module, so that a test
    can set the variable after the module is already loaded.
    """
    return _truthy(os.environ.get(name), default)


def is_enabled() -> bool:
    """Whether archiving should run at all.

    Default-on in normal operation and default-off under pytest, so that a test
    suite cannot quietly fill the repository with archive directories or, worse,
    copy a live workbench's 55 MB prompt log on every reset fixture. A test that
    wants the archiver sets ``CAT_GAME_ARCHIVE=1`` explicitly, along with
    ``CAT_GAME_ARCHIVE_DIR`` pointing at a temp directory.
    """
    under_pytest = "PYTEST_CURRENT_TEST" in os.environ
    return env_flag("CAT_GAME_ARCHIVE", default=not under_pytest)



def archive_root() -> Path:
    return Path(os.environ.get("CAT_GAME_ARCHIVE_DIR") or (REPO_ROOT / "archive"))


def cat_runtime_dir(agent_id: Optional[str] = None) -> Path:
    """The cat actor's runtime directory.

    Deliberately resolved here rather than imported from ``actors.cat.store``:
    the game server should not take a package dependency on the actor just to
    learn a path. The duplication is held in check by a test that asserts the
    two agree, so a change to either side fails loudly instead of silently
    archiving an empty directory.
    """
    override = os.environ.get("CAT_AGENT_RUNTIME_DIR")
    tmp_base = Path(tempfile.gettempdir())

    if agent_id:
        if override:
            override_path = Path(override)
            if override_path.name in (agent_id, f"cm_runtime_{agent_id}", f"runtime_{agent_id}"):
                return override_path
            for sub in (agent_id, f"cm_runtime_{agent_id}", f"runtime_{agent_id}"):
                cand = override_path / sub
                if cand.is_dir():
                    return cand
            agent_tmp = tmp_base / f"cm_runtime_{agent_id}"
            if agent_tmp.is_dir():
                return agent_tmp
            agent_legacy_tmp = tmp_base / f"runtime_{agent_id}"
            if agent_legacy_tmp.is_dir():
                return agent_legacy_tmp
            repo_agent = REPO_ROOT / "actors" / "cat" / f"runtime_{agent_id}"
            if repo_agent.is_dir():
                return repo_agent
            if agent_id == "cat-1" and override_path.is_dir():
                return override_path
            return agent_tmp

        agent_tmp = tmp_base / f"cm_runtime_{agent_id}"
        if agent_tmp.is_dir():
            return agent_tmp
        agent_legacy_tmp = tmp_base / f"runtime_{agent_id}"
        if agent_legacy_tmp.is_dir():
            return agent_legacy_tmp
        repo_agent = REPO_ROOT / "actors" / "cat" / f"runtime_{agent_id}"
        if repo_agent.is_dir():
            return repo_agent
        if agent_id == "cat-1":
            cm_runtime = tmp_base / "cm_runtime"
            if cm_runtime.is_dir():
                return cm_runtime
        return agent_tmp

    if override:
        return Path(override)
    return REPO_ROOT / "actors" / "cat" / "runtime"


def _jsonable(value: Any) -> Any:
    """Coerces pydantic models, enums and sets into JSON-safe structures."""
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def _sha256(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def _git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except Exception:
        return None


def _git_provenance() -> Dict[str, Any]:
    """Which code produced this game.

    ``dirty`` matters more than the commit here. Most runs happen on uncommitted
    edits, so a commit alone would imply a reproducibility the archive does not
    have; the markdown copies below are what actually pin the prompt.
    """
    status = _git("status", "--porcelain")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def _prompt_sources() -> List[Path]:
    """Every markdown file in the actor package: the prompt, in source form.

    Globbed rather than listed so a new skills file is captured the day it is
    added rather than the day someone remembers to update this module. The
    runtime directory is excluded because its markdown is output, not input,
    and is copied separately.
    """
    actor_dir = REPO_ROOT / "actors"
    if not actor_dir.is_dir():
        return []
    runtime = cat_runtime_dir().resolve()
    found = []
    for path in sorted(actor_dir.rglob("*.md")):
        try:
            if runtime in path.resolve().parents:
                continue
        except OSError:
            continue
        found.append(path)
    return found


def capture(sim: Any, reason: str) -> Optional[Dict[str, Any]]:
    """Snapshots everything that lives in memory. Must run before any mutation.

    Split from :func:`write` because the two have opposite constraints. This
    half is cheap but order-critical: it has to happen before ``stats.reset()``
    destroys the counters. The other half is slow but order-free, so it can be
    handed to a thread.

    Returns None for a game with no ticks in it, so that double-clicking reset
    leaves one archive rather than two.
    """
    tick = int(getattr(sim.scheduler, "tick", 0) or 0)
    if tick <= 0:
        return None

    try:
        boxscore = _jsonable(sim.get_boxscore())
    except Exception as exc:
        logger.warning("Archive: box score unavailable: %s", exc)
        boxscore = None

    agents: Dict[str, str] = {}
    try:
        if hasattr(sim, "entity_manager"):
            for entity in sim.entity_manager.entities.values():
                etype = getattr(entity.type, "value", entity.type)
                if str(etype).lower() == "cat":
                    agents[entity.id] = sim.entity_manager.get_agent_mode(entity.id)
            for em_id in getattr(sim.entity_manager, "agent_modes", {}):
                if str(em_id).startswith("cat-") and em_id not in agents:
                    agents[em_id] = sim.entity_manager.get_agent_mode(em_id)
            cat_count = getattr(sim.entity_manager, "_next_id", {}).get("cat", 1)
            for i in range(1, cat_count):
                cid = f"cat-{i}"
                if cid not in agents:
                    agents[cid] = sim.entity_manager.get_agent_mode(cid)
        if hasattr(sim, "stats"):
            cats_dict = getattr(sim.stats, "cats", {}) or {}
            for cat_id in cats_dict.keys():
                if cat_id not in agents:
                    mode = "unknown"
                    if hasattr(sim, "entity_manager") and hasattr(sim.entity_manager, "get_agent_mode"):
                        try:
                            mode = sim.entity_manager.get_agent_mode(cat_id)
                        except Exception:
                            pass
                    agents[cat_id] = mode
    except Exception as exc:
        logger.warning("Archive: agent modes unavailable: %s", exc)

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    return {
        "dir_name": f"{stamp}__tick{tick:05d}__{sim.simulation_id}",
        "manifest": {
            "schema": MANIFEST_SCHEMA_VERSION,
            "simulation_id": sim.simulation_id,
            # Why the game ended. "reset" is a deliberate restart; "shutdown"
            # means the server process went away, which on this project usually
            # means a .py edit tripped uvicorn's reloader mid-run.
            "reason": reason,
            "archived_at": now.isoformat().replace("+00:00", "Z"),
            "final": {
                "tick": tick,
                "round": int(getattr(sim.scheduler, "round", 0) or 0),
                "captures": int(getattr(sim.scheduler, "captures", 0) or 0),
                "status": _jsonable(getattr(sim, "status", None)),
                "scores": _jsonable(dict(getattr(sim.scheduler, "scores", {}) or {})),
                "mice_remaining": len(
                    [
                        e
                        for e in sim.entity_manager.entities.values()
                        if str(getattr(e.type, "value", e.type)).lower() == "mouse"
                    ]
                ),
            },
            "arena": {
                "width": sim.world.width,
                "height": sim.world.height,
                "topology": _jsonable(sim.world.topology),
            },
            "settings": {
                "tick_rate": sim.config.tick_rate,
                "populations": _jsonable(dict(sim.config.populations or {})),
                "species": _jsonable(sim.config.species),
                "disclosure": _jsonable(sim.config.disclosure),
            },
            # Which cat was the model and which was the heuristic. Without this
            # a box score comparison can be read exactly backwards.
            "agents": agents,
            "code": _git_provenance(),
        },
        "boxscore": boxscore,
    }


def _archive_runtime_files(
    src_dir: Path,
    target_dir: Path,
) -> Dict[str, Any]:
    """Copies or compresses runtime artefacts from a source directory into target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    file_records: Dict[str, Any] = {}
    for name, may_compress in RUNTIME_ARTEFACTS:
        src = src_dir / name
        if not src.exists():
            # Recorded rather than omitted: "the actor never wrote one" and
            # "the archiver forgot to look" must not look the same later.
            file_records[name] = {"present": False}
            continue
        try:
            raw_bytes = src.stat().st_size
            if may_compress and raw_bytes >= GZIP_THRESHOLD_BYTES:
                out = target_dir / f"{name}.gz"
                with src.open("rb") as fh_in, gzip.open(out, "wb", compresslevel=6) as fh_out:
                    shutil.copyfileobj(fh_in, fh_out, length=1024 * 1024)
            else:
                out = target_dir / name
                shutil.copyfile(src, out)
            file_records[name] = {
                "present": True,
                "stored_as": out.name,
                "bytes": raw_bytes,
                "stored_bytes": out.stat().st_size,
                "sha256": _sha256(src),
            }
        except Exception as exc:
            logger.warning("Archive: failed to copy %s: %s", name, exc)
            file_records[name] = {"present": True, "error": str(exc)}
    return file_records


def write(captured: Dict[str, Any]) -> Path:
    """Writes the archive to disk. Slow, blocking, safe to run in a thread."""
    dest = archive_root() / captured["dir_name"]
    dest.mkdir(parents=True, exist_ok=True)

    manifest = dict(captured["manifest"])
    files: Dict[str, Any] = {}

    if captured.get("boxscore") is not None:
        (dest / "boxscore.json").write_text(
            json.dumps(captured["boxscore"], indent=2), encoding="utf-8"
        )
        files["boxscore.json"] = {"source": "memory"}

    agents = manifest.get("agents") or {}
    agent_ids = sorted(agents.keys()) if agents else []
    agent_files: Dict[str, Dict[str, Any]] = {}

    if agent_ids:
        for cat_id in agent_ids:
            cat_dir = cat_runtime_dir(cat_id)
            cat_dest = dest / cat_id
            records = _archive_runtime_files(cat_dir, cat_dest)
            agent_files[cat_id] = records

        # Maintain root copies for backwards compatibility
        primary_id = "cat-1" if "cat-1" in agent_ids else agent_ids[0]
        primary_records = agent_files.get(primary_id, {})
        for name, rec in primary_records.items():
            if rec.get("present"):
                stored_as = rec.get("stored_as", name)
                cat_file = dest / primary_id / stored_as
                root_file = dest / stored_as
                if cat_file.exists() and not root_file.exists():
                    try:
                        os.link(cat_file, root_file)
                    except Exception:
                        shutil.copyfile(cat_file, root_file)
            files[name] = dict(rec)
    else:
        runtime = cat_runtime_dir()
        root_records = _archive_runtime_files(runtime, dest)
        files.update(root_records)

    prompts_dir = dest / "prompt_sources"
    prompt_files: Dict[str, Any] = {}
    for src in _prompt_sources():
        try:
            rel = src.relative_to(REPO_ROOT / "actors")
            out = prompts_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
            prompt_files[str(rel)] = {
                "bytes": src.stat().st_size,
                "sha256": _sha256(src),
            }
        except Exception as exc:
            logger.warning("Archive: failed to copy prompt source %s: %s", src, exc)

    manifest["files"] = files
    if agent_files:
        manifest["agent_files"] = agent_files
    manifest["prompt_sources"] = prompt_files
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    _update_latest_symlink(dest)
    return dest


def refresh_latest_symlink() -> Optional[str]:
    """Updates the 'latest' symlink to point to the newest archive directory, or unlinks it."""
    root = archive_root()
    link = root / "latest"
    if not root.exists():
        if link.is_symlink() or link.exists():
            try:
                link.unlink()
            except Exception:
                pass
        return None

    dirs = sorted([p for p in root.iterdir() if p.is_dir() and not p.is_symlink()], key=lambda p: p.name, reverse=True)
    if not dirs:
        if link.is_symlink() or link.exists():
            try:
                link.unlink()
            except Exception:
                pass
        return None

    newest = dirs[0]
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(newest.name)
        return newest.name
    except Exception:
        return None


def _update_latest_symlink(dest: Path) -> None:
    """Points ``archive/latest`` at the newest archive, for ad-hoc analysis.

    Best effort. A failure here costs a convenience shortcut, not the archive.
    """
    refresh_latest_symlink()


def list_archives() -> List[Dict[str, Any]]:
    """Lists all archived games on disk, sorted newest first."""
    root = archive_root()
    if not root.exists():
        return []

    latest_name = None
    link = root / "latest"
    if link.is_symlink():
        try:
            latest_name = os.readlink(link)
        except OSError:
            pass

    results = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.is_symlink():
            continue

        folder_name = entry.name
        manifest_file = entry / "manifest.json"
        manifest_data = None
        if manifest_file.exists():
            try:
                manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        total_bytes = 0
        file_count = 0
        try:
            for f in entry.rglob("*"):
                if f.is_file():
                    total_bytes += f.stat().st_size
                    file_count += 1
        except Exception:
            pass

        final_info = (manifest_data or {}).get("final", {})
        archived_at = (manifest_data or {}).get("archived_at")
        if not archived_at:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
            archived_at = mtime.isoformat().replace("+00:00", "Z")

        has_notes = (entry / "coaching_notes.md").exists() or any(
            (p / "coaching_notes.md").exists() for p in entry.iterdir() if p.is_dir()
        )
        has_coach_memory = (entry / "coach_memory.jsonl").exists() or any(
            (p / "coach_memory.jsonl").exists() for p in entry.iterdir() if p.is_dir()
        )
        has_run_config = (entry / "run_config.json").exists() or any(
            (p / "run_config.json").exists() for p in entry.iterdir() if p.is_dir()
        )

        results.append({
            "id": folder_name,
            "dir_name": folder_name,
            "archived_at": archived_at,
            "simulation_id": (manifest_data or {}).get("simulation_id", "unknown"),
            "reason": (manifest_data or {}).get("reason", "unknown"),
            "tick": final_info.get("tick", 0),
            "round": final_info.get("round", 0),
            "captures": final_info.get("captures", 0),
            "scores": final_info.get("scores", {}),
            "mice_remaining": final_info.get("mice_remaining", 0),
            "agents": (manifest_data or {}).get("agents", {}),
            "has_boxscore": (entry / "boxscore.json").exists(),
            "has_notes": has_notes,
            "has_coach_memory": has_coach_memory,
            "has_run_config": has_run_config,
            "total_size_bytes": total_bytes,
            "files_count": file_count,
            "is_latest": folder_name == latest_name,
        })

    results.sort(key=lambda x: x["dir_name"], reverse=True)
    return results


def get_archive_detail(archive_id: str) -> Optional[Dict[str, Any]]:
    """Loads detailed information for a single archive run."""
    root = archive_root()
    dest = (root / archive_id).resolve()
    if not dest.is_relative_to(root.resolve()) or not dest.is_dir() or dest.is_symlink():
        return None

    manifest_file = dest / "manifest.json"
    manifest = None
    if manifest_file.exists():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    boxscore_file = dest / "boxscore.json"
    boxscore = None
    if boxscore_file.exists():
        try:
            boxscore = json.loads(boxscore_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    run_config_file = dest / "run_config.json"
    run_config = None
    if run_config_file.exists():
        try:
            run_config = json.loads(run_config_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    notes_file = dest / "coaching_notes.md"
    coaching_notes = None
    if notes_file.exists():
        try:
            coaching_notes = notes_file.read_text(encoding="utf-8")
        except Exception:
            pass

    coach_memory_file = dest / "coach_memory.jsonl"
    coach_memories = None
    if coach_memory_file.exists():
        try:
            coach_memories = []
            for line in coach_memory_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    coach_memories.append(json.loads(line))
        except Exception:
            pass

    agents_data: Dict[str, Any] = {}
    for agent_dir in sorted(p for p in dest.iterdir() if p.is_dir() and not p.is_symlink() and p.name.startswith("cat-")):
        agent_id = agent_dir.name
        a_notes = None
        a_notes_file = agent_dir / "coaching_notes.md"
        if a_notes_file.exists():
            try:
                a_notes = a_notes_file.read_text(encoding="utf-8")
            except Exception:
                pass
        a_memories = None
        a_mem_file = agent_dir / "coach_memory.jsonl"
        if a_mem_file.exists():
            try:
                a_memories = [json.loads(l) for l in a_mem_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            except Exception:
                pass
        a_run_config = None
        a_rc_file = agent_dir / "run_config.json"
        if a_rc_file.exists():
            try:
                a_run_config = json.loads(a_rc_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        agents_data[agent_id] = {
            "coaching_notes": a_notes,
            "coach_memories": a_memories,
            "run_config": a_run_config,
            "has_transcript": (agent_dir / "transcript.jsonl").exists(),
            "has_prompts": (agent_dir / "prompts.jsonl").exists() or (agent_dir / "prompts.jsonl.gz").exists(),
        }

    # If multiple agents have coaching notes, provide unified view
    if agents_data and len(agents_data) > 1:
        notes_parts = []
        for aid, adata in agents_data.items():
            if adata.get("coaching_notes"):
                notes_parts.append(f"### {aid} Coaching Notes\n\n{adata['coaching_notes']}")
        if notes_parts:
            coaching_notes = "\n\n---\n\n".join(notes_parts)

    files = []
    total_bytes = 0
    for f in sorted(dest.rglob("*")):
        if f.is_file():
            size = f.stat().st_size
            total_bytes += size
            rel = str(f.relative_to(dest))
            files.append({
                "path": rel,
                "name": f.name,
                "size_bytes": size,
            })

    latest_name = None
    link = root / "latest"
    if link.is_symlink():
        try:
            latest_name = os.readlink(link)
        except OSError:
            pass

    return {
        "id": archive_id,
        "manifest": manifest,
        "boxscore": boxscore,
        "run_config": run_config,
        "coaching_notes": coaching_notes,
        "coach_memories": coach_memories,
        "agent_details": agents_data,
        "files": files,
        "total_size_bytes": total_bytes,
        "is_latest": archive_id == latest_name,
    }


def delete_archive_run(archive_id: str) -> bool:
    """Removes an archive directory from disk and refreshes the latest symlink."""
    root = archive_root()
    dest = (root / archive_id).resolve()
    if not dest.is_relative_to(root.resolve()) or not dest.is_dir() or dest.is_symlink():
        return False

    shutil.rmtree(dest)
    refresh_latest_symlink()
    return True


def wipe_all_archives() -> int:
    """Wipes all archived simulation runs from disk and unlinks latest."""
    root = archive_root()
    if not root.exists():
        return 0

    count = 0
    for entry in list(root.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            try:
                shutil.rmtree(entry)
                count += 1
            except Exception as exc:
                logger.warning("Failed to remove archive %s: %s", entry, exc)

    link = root / "latest"
    if link.is_symlink() or link.exists():
        try:
            link.unlink()
        except Exception:
            pass

    return count


async def archive_game(sim: Any, reason: str) -> Optional[Path]:
    """Archives a finished game. Never raises.

    The in-memory snapshot is taken on the event loop, because it must observe
    the simulation before the caller mutates it. The file copying is then
    awaited on a worker thread: awaited, not fired and forgotten, because the
    cat actor wipes its runtime files shortly after it notices the reset, and a
    detached copy could lose that race.
    """
    if not is_enabled():
        return None
    try:
        captured = capture(sim, reason)
    except Exception as exc:
        logger.warning("Archive: capture failed, game not archived: %s", exc)
        return None
    if captured is None:
        return None
    try:
        dest = await asyncio.to_thread(write, captured)
    except Exception as exc:
        logger.warning("Archive: write failed, game not archived: %s", exc)
        return None

    msg = f"🗄️  [ARCHIVE] {reason}: saved game to {dest}"
    logger.info(msg)
    print(msg, flush=True)
    return dest
