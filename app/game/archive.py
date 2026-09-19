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


def cat_runtime_dir() -> Path:
    """The cat actor's runtime directory.

    Deliberately resolved here rather than imported from ``actors.cat.store``:
    the game server should not take a package dependency on the actor just to
    learn a path. The duplication is held in check by a test that asserts the
    two agree, so a change to either side fails loudly instead of silently
    archiving an empty directory.
    """
    override = os.environ.get("CAT_AGENT_RUNTIME_DIR")
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
        for entity in sim.entity_manager.entities.values():
            etype = getattr(entity.type, "value", entity.type)
            if str(etype).lower() == "cat":
                agents[entity.id] = sim.entity_manager.get_agent_mode(entity.id)
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

    runtime = cat_runtime_dir()
    for name, may_compress in RUNTIME_ARTEFACTS:
        src = runtime / name
        if not src.exists():
            # Recorded rather than omitted: "the actor never wrote one" and
            # "the archiver forgot to look" must not look the same later.
            files[name] = {"present": False}
            continue
        try:
            raw_bytes = src.stat().st_size
            if may_compress and raw_bytes >= GZIP_THRESHOLD_BYTES:
                out = dest / f"{name}.gz"
                with src.open("rb") as fh_in, gzip.open(out, "wb", compresslevel=6) as fh_out:
                    shutil.copyfileobj(fh_in, fh_out, length=1024 * 1024)
            else:
                out = dest / name
                shutil.copyfile(src, out)
            files[name] = {
                "present": True,
                "stored_as": out.name,
                "bytes": raw_bytes,
                "stored_bytes": out.stat().st_size,
                "sha256": _sha256(src),
            }
        except Exception as exc:
            logger.warning("Archive: failed to copy %s: %s", name, exc)
            files[name] = {"present": True, "error": str(exc)}

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
    manifest["prompt_sources"] = prompt_files
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    _update_latest_symlink(dest)
    return dest


def _update_latest_symlink(dest: Path) -> None:
    """Points ``archive/latest`` at the newest archive, for ad-hoc analysis.

    Best effort. A failure here costs a convenience shortcut, not the archive.
    """
    link = archive_root() / "latest"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(dest.name)
    except Exception:
        pass


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
