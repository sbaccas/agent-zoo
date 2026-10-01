import asyncio
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.admin import get_simulation_or_404
from app.game.archive import (
    archive_game,
    archive_root,
    delete_archive_run,
    get_archive_detail,
    list_archives,
    wipe_all_archives,
)

router = APIRouter(prefix="/api/archives", tags=["Archives"])

ARCHIVE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")


class CaptureArchiveRequest(BaseModel):
    reason: str = Field(default="manual_capture", max_length=100)
    sim_id: str = Field(default="active", max_length=100)


def _validate_archive_id(archive_id: str) -> Path:
    if not ARCHIVE_ID_PATTERN.match(archive_id) or ".." in archive_id:
        raise HTTPException(status_code=400, detail="Invalid archive ID format")
    root = archive_root().resolve()
    target = (root / archive_id).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Path traversal detected")
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Archive '{archive_id}' not found")
    return target


@router.get("")
async def get_all_archives() -> Dict[str, Any]:
    """Returns a list of all archived simulation runs on disk, sorted newest first."""
    archives = await asyncio.to_thread(list_archives)
    return {
        "total": len(archives),
        "archive_root": str(archive_root()),
        "archives": archives,
    }


@router.post("/capture")
async def capture_active_simulation(request: Optional[CaptureArchiveRequest] = None) -> Dict[str, Any]:
    """Manually snapshots the current simulation state and actor runtime files to disk."""
    reason = request.reason if request and request.reason else "manual_capture"
    sim_id = request.sim_id if request and request.sim_id else "active"

    sim = get_simulation_or_404(sim_id)
    tick = int(getattr(sim.scheduler, "tick", 0) or 0)
    if tick <= 0:
        raise HTTPException(
            status_code=400,
            detail="Simulation has not ticked yet (tick 0). Start or step the simulation before archiving.",
        )

    dest = await archive_game(sim, reason=reason)
    if not dest:
        raise HTTPException(status_code=500, detail="Failed to capture archive snapshot")

    return {
        "success": True,
        "archive_id": dest.name,
        "path": str(dest),
        "tick": tick,
        "reason": reason,
    }


@router.get("/{archive_id}")
async def get_archive(archive_id: str) -> Dict[str, Any]:
    """Returns detailed information, manifest, and file listings for a specific archive."""
    target_dir = _validate_archive_id(archive_id)
    actual_id = target_dir.name
    detail = await asyncio.to_thread(get_archive_detail, actual_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Archive '{archive_id}' not found")
    return detail


@router.get("/{archive_id}/replay")
async def get_archive_replay(
    archive_id: str,
    from_tick: Optional[int] = Query(None, ge=1),
    to_tick: Optional[int] = Query(None, ge=1),
) -> Dict[str, Any]:
    """Returns synchronized tick-by-tick replay frames, multi-agent POV data, and death moments."""
    from app.game.replay import build_archive_replay

    target_dir = _validate_archive_id(archive_id)
    actual_id = target_dir.name
    replay_data = await asyncio.to_thread(build_archive_replay, actual_id, from_tick, to_tick)
    if not replay_data:
        raise HTTPException(status_code=404, detail=f"Could not build replay for archive '{archive_id}'")
    return replay_data


@router.get("/{archive_id}/files/{file_path:path}")
async def get_archive_file(archive_id: str, file_path: str):
    """Securely downloads or previews an artifact file stored within an archive."""
    target_dir = _validate_archive_id(archive_id)
    
    # Path traversal protection
    requested_file = (target_dir / file_path).resolve()
    if not requested_file.is_relative_to(target_dir):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not requested_file.is_file():
        raise HTTPException(status_code=404, detail=f"File '{file_path}' not found in archive")

    media_type = "application/octet-stream"
    name_lower = requested_file.name.lower()
    if name_lower.endswith(".json"):
        media_type = "application/json"
    elif name_lower.endswith(".md") or name_lower.endswith(".txt"):
        media_type = "text/plain; charset=utf-8"
    elif name_lower.endswith(".jsonl"):
        media_type = "application/x-ndjson"
    elif name_lower.endswith(".gz"):
        media_type = "application/gzip"

    return FileResponse(
        path=requested_file,
        filename=requested_file.name,
        media_type=media_type,
    )


@router.delete("/{archive_id}")
async def delete_archive(archive_id: str) -> Dict[str, Any]:
    """Permanently deletes a single archive run directory from disk."""
    target_dir = _validate_archive_id(archive_id)
    actual_id = target_dir.name
    deleted = await asyncio.to_thread(delete_archive_run, actual_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Could not delete archive '{archive_id}'")
    return {"success": True, "deleted": actual_id}


@router.delete("")
@router.post("/wipe")
async def wipe_archives() -> Dict[str, Any]:
    """Wipes all archived simulation runs from disk."""
    count = await asyncio.to_thread(wipe_all_archives)
    return {"success": True, "deleted_count": count}
