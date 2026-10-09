"""Cat & Mouse Model Context Protocol (MCP) Server.

Exposes game engine analytics, live tactical telemetry, and coach memory
as standardized MCP Tools and Resources over SSE/HTTP transport (port 9050).
Includes real-time dashboard UI and event streaming at http://localhost:9050/.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from actors.auth import get_gcp_id_token
from actors.cat import store
from app.mcp.telemetry import get_telemetry

logger = logging.getLogger("cat_mcp_server")

DEFAULT_MCP_PORT = 9050
DEFAULT_GAME_SERVER_URL = "http://127.0.0.1:8000"
GAME_SERVER_URL_ENV = "GAME_SERVER_URL"
MCP_PORT_ENV = "CAT_MCP_PORT"
HTTP_TIMEOUT_SECONDS = 3.0

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"
CHAT_HTML_PATH = STATIC_DIR / "chat.html"
NOTES_HTML_PATH = STATIC_DIR / "notes.html"


def get_game_server_url() -> str:
    return (
        os.environ.get(GAME_SERVER_URL_ENV)
        or os.environ.get("GAME_SERVER_URL")
        or DEFAULT_GAME_SERVER_URL
    ).rstrip("/")


async def _fetch_from_game_server(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    method: str = "GET",
    json_body: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Any, Optional[str]]:
    """Helper to query or mutate the authoritative game server over HTTP."""
    base = get_game_server_url()
    url = f"{base}{endpoint}"
    token = get_gcp_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else None

    # Filter out None query parameters
    query_params = {k: v for k, v in (params or {}).items() if v is not None}

    try:
        async with httpx.AsyncClient(headers=headers, timeout=HTTP_TIMEOUT_SECONDS) as client:
            m = method.upper()
            if m == "PATCH":
                resp = await client.patch(url, params=query_params, json=json_body)
            elif m == "POST":
                resp = await client.post(url, params=query_params, json=json_body)
            else:
                resp = await client.get(url, params=query_params)

            if resp.status_code == 403:
                return False, None, "feature or resource disclosure is disabled (HTTP 403)"
            if resp.status_code not in (200, 201, 204):
                return False, None, f"game server returned HTTP {resp.status_code}"
            return True, resp.json() if resp.content else {}, None
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


async def _build_notice_board_data(sim_id: str = "active") -> Tuple[Dict[str, Any], str]:
    """Queries game server species and state, generates bulletin text, and syncs notice_board.md."""
    ok_species, species_data, err_species = await _fetch_from_game_server(f"/api/simulations/{sim_id}/species")
    ok_state, state_data, err_state = await _fetch_from_game_server(f"/api/simulations/{sim_id}/state")

    tick = state_data.get("tick", 0) if ok_state and isinstance(state_data, dict) else 0
    cat_species = species_data.get("cat", {}) if ok_species and isinstance(species_data, dict) else {}

    absorption_active = bool(cat_species.get("absorption", False))
    aim_assist_active = bool(cat_species.get("aim_assist", False))
    mouse_heal = int(cat_species.get("heal_on_mouse_capture", 15))
    cat_heal = int(cat_species.get("heal_on_cat_elimination", 90))
    healing_active = mouse_heal > 0 or cat_heal > 0

    # Format bulletin
    bulletin_lines = [
        f"# 📢 GLOBAL ARENA NOTICE BOARD (Tick {tick})",
        "",
        "### ⚔️ Active Arena Rules & Modifiers:",
    ]
    if absorption_active:
        bulletin_lines.append(
            "- **KILL ABSORPTION**: **ACTIVE**. When a cat knocks out a rival cat, the victor absorbs ALL accumulated prey kills into their score! Rival cats carrying kills are prime high-value targets."
        )
    else:
        bulletin_lines.append(
            "- **KILL ABSORPTION**: INACTIVE. Takedowns award standard elimination points (+1)."
        )

    if aim_assist_active:
        bulletin_lines.append(
            "- **AIM ASSIST**: ACTIVE. Observation contains authoritative pre-computed forward, offaxis, and in_strike_zone tags."
        )

    if healing_active:
        bulletin_lines.append(
            f"- **PREDATION HP RESTORATION**: **ACTIVE**. Eating prey or rival predators restores vital health: "
            f"Capturing a mouse restores **+{mouse_heal} HP**. Eliminating a rival cat restores **+{cat_heal} HP** (capped at 100 HP max). "
            f"Hunting mice allows recovering from combat wounds."
        )

    bulletin_text = "\n".join(bulletin_lines)

    # Sync with on-disk store so all agents pick it up in their next prompt turn
    store.write_notice_board(bulletin_text)

    notice_data = {
        "available": ok_species,
        "tick": tick,
        "active_modifiers": {
            "absorption": {
                "name": "Kill Absorption",
                "status": "ACTIVE" if absorption_active else "INACTIVE",
                "enabled": absorption_active,
                "description": "Knocking out a rival predator absorbs their entire kill count into your score.",
            },
            "aim_assist": {
                "name": "Aim Assist",
                "status": "ACTIVE" if aim_assist_active else "INACTIVE",
                "enabled": aim_assist_active,
                "description": "Pre-computes relative strike geometry in observer coordinates.",
            },
            "predation_healing": {
                "name": "Predation HP Restoration",
                "status": "ACTIVE" if healing_active else "INACTIVE",
                "enabled": healing_active,
                "mouse_heal": mouse_heal,
                "cat_heal": cat_heal,
                "description": f"Capturing a mouse restores +{mouse_heal} HP; eliminating a rival cat restores +{cat_heal} HP (capped at max HP).",
            },
        },
        "bulletin": bulletin_text,
    }
    return notice_data, bulletin_text


def create_mcp_server(name: str = "cat-and-mouse-mcp", version: str = "0.1.0") -> MCPServer:
    """Builds and registers all tools and resources on an MCPServer instance."""
    server = MCPServer(
        name=name,
        version=version,
        instructions="Authoritative simulation analytics and coach memory server for Cat & Mouse.",
    )

    # --------------------------------------------------------------------------
    # Tools
    # --------------------------------------------------------------------------

    @server.tool(
        name="get_boxscore",
        description="Reads the game engine's box score for the active game, covering both cats' KPIs, accuracy, kills, and movement stats.",
    )
    async def get_boxscore(sim_id: str = "active") -> Dict[str, Any]:
        """Fetch boxscore KPIs for the specified simulation."""
        start = time.perf_counter()
        ok, payload, err = await _fetch_from_game_server(f"/api/simulations/{sim_id}/boxscore")
        dur = round((time.perf_counter() - start) * 1000, 2)
        telemetry = get_telemetry()

        if not ok:
            res = {"available": False, "reason": err}
            telemetry.record_event(
                event_type="tool_call",
                level="warn",
                tool="get_boxscore",
                arguments={"sim_id": sim_id},
                duration_ms=dur,
                status="warning",
                summary=f"get_boxscore ({dur}ms): {err}",
                details={"arguments": {"sim_id": sim_id}, "result": res},
            )
            return res

        if not isinstance(payload, dict):
            res = {"available": False, "reason": "unexpected payload format"}
            telemetry.record_event(
                event_type="tool_call",
                level="error",
                tool="get_boxscore",
                arguments={"sim_id": sim_id},
                duration_ms=dur,
                status="error",
                summary=f"get_boxscore error: unexpected payload format",
                details={"arguments": {"sim_id": sim_id}, "result": res},
            )
            return res

        tick = payload.get("tick")
        since = payload.get("tracking_since_tick")
        measured = tick - since if isinstance(tick, int) and isinstance(since, int) else None

        res = {
            "available": True,
            "simulation_id": payload.get("simulation_id"),
            "status": payload.get("status"),
            "tick": tick,
            "round": payload.get("round"),
            "tracking_since_tick": since,
            "ticks_measured": measured,
            "arena": payload.get("arena"),
            "leader": payload.get("leader"),
            "cats": payload.get("cats", []),
            "mice_alive": payload.get("mice_alive"),
            "mice_initial": payload.get("mice_initial"),
            "captures": payload.get("captures"),
        }
        leader = res.get("leader") or "none"
        tick = res.get("tick", 0)
        telemetry.record_event(
            event_type="tool_call",
            level="success",
            tool="get_boxscore",
            arguments={"sim_id": sim_id},
            duration_ms=dur,
            status="ok",
            summary=f"get_boxscore ({dur}ms) -> tick: {tick}, leader: {leader}",
            details={"arguments": {"sim_id": sim_id}, "result": res},
        )
        return res

    @server.tool(
        name="get_coverage_map",
        description="Inspects the spatial grid heatmap showing where cats have spent their ticks across the arena.",
    )
    async def get_coverage_map(sim_id: str = "active", cat_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetch spatial coverage heatmap."""
        start = time.perf_counter()
        ok, payload, err = await _fetch_from_game_server(f"/api/simulations/{sim_id}/heatmap")
        dur = round((time.perf_counter() - start) * 1000, 2)
        telemetry = get_telemetry()

        if not ok:
            res = {"available": False, "reason": err}
            telemetry.record_event(
                event_type="tool_call",
                level="warn",
                tool="get_coverage_map",
                arguments={"sim_id": sim_id, "cat_id": cat_id},
                duration_ms=dur,
                status="warning",
                summary=f"get_coverage_map ({dur}ms): {err}",
                details={"arguments": {"sim_id": sim_id, "cat_id": cat_id}, "result": res},
            )
            return res

        if not isinstance(payload, dict):
            res = {"available": False, "reason": "unexpected payload format"}
            return res

        raw_cats = payload.get("cats", [])
        if cat_id and isinstance(raw_cats, list):
            raw_cats = [c for c in raw_cats if c.get("id") == cat_id]

        sanitized_cats = []
        for c in (raw_cats if isinstance(raw_cats, list) else []):
            sc = dict(c)
            sc.pop("path", None)  # Prevent exact coordinate trail leak
            sanitized_cats.append(sc)

        res = {
            "available": True,
            "simulation_id": payload.get("simulation_id"),
            "tick": payload.get("tick"),
            "ascii": payload.get("ascii") if not cat_id else "\n\n".join(c.get("ascii", "") for c in sanitized_cats),
            "arena": payload.get("arena"),
            "grid_size": payload.get("grid_size"),
            "cell_size": payload.get("cell_size"),
            "cats": sanitized_cats,
        }
        grid = res.get("grid_size") or "unknown"
        telemetry.record_event(
            event_type="tool_call",
            level="success",
            tool="get_coverage_map",
            arguments={"sim_id": sim_id},
            duration_ms=dur,
            status="ok",
            summary=f"get_coverage_map ({dur}ms) -> grid: {grid}",
            details={"arguments": {"sim_id": sim_id}, "result": res},
        )
        return res

    @server.tool(
        name="get_event_reel",
        description="Retrieves granular combat events (attacks, whiffs, kills, damage) and death logs from the simulation event buffer.",
    )
    async def get_event_reel(
        limit: Optional[int] = 20,
        since_tick: Optional[int] = None,
        actor_id: Optional[str] = None,
        event_type: Optional[str] = None,
        sim_id: str = "active",
    ) -> Dict[str, Any]:
        """Fetch filtered historical game events and combat actions."""
        start = time.perf_counter()
        params = {
            "limit": limit,
            "since_tick": since_tick,
            "actor_id": actor_id,
            "event_type": event_type,
        }
        ok, payload, err = await _fetch_from_game_server(
            f"/api/simulations/{sim_id}/events", params=params
        )
        dur = round((time.perf_counter() - start) * 1000, 2)
        telemetry = get_telemetry()

        if not ok:
            res = {"available": False, "reason": err}
            telemetry.record_event(
                event_type="tool_call",
                level="warn",
                tool="get_event_reel",
                arguments=params,
                duration_ms=dur,
                status="warning",
                summary=f"get_event_reel ({dur}ms): {err}",
                details={"arguments": params, "result": res},
            )
            return res

        if not isinstance(payload, dict):
            res = {"available": False, "reason": "unexpected payload format"}
            return res

        events = payload.get("events", [])
        res = {
            "available": True,
            "simulation_id": payload.get("simulation_id"),
            "tick": payload.get("tick"),
            "round": payload.get("round"),
            "events": events,
            "deaths": payload.get("deaths", []),
            "entity_events": payload.get("entity_events", {}),
        }
        telemetry.record_event(
            event_type="tool_call",
            level="success",
            tool="get_event_reel",
            arguments=params,
            duration_ms=dur,
            status="ok",
            summary=f"get_event_reel ({dur}ms) -> {len(events)} events (tick {payload.get('tick')})",
            details={"arguments": params, "result": res},
        )
        return res

    @server.tool(
        name="write_coach_note",
        description="Records a private entry to the Coach's tactical notebook (coach_memory.jsonl). Call this during evaluation to log key hypotheses, spatial deductions, mouse clustering, and observations for your future self across cycles. Supports linking specific transcript tick numbers (linked_ticks=[...]) to capture their contextual snapshots for future RAG recall. (Note: These are distinct from the actor's published standing orders in coaching_notes.md).",
    )
    async def write_coach_note(
        note: str,
        category: str = "tactical",
        tags: Optional[List[str]] = None,
        tick: Optional[int] = None,
        linked_ticks: Optional[List[int]] = None,
        cat_id: Optional[str] = "cat-1",
    ) -> Dict[str, Any]:
        """Save a coach memory note to disk with optional linked transcript ticks."""
        start = time.perf_counter()
        record = store.append_coach_memory(
            note=note, category=category, tags=tags, tick=tick, linked_ticks=linked_ticks, agent_id=cat_id
        )
        dur = round((time.perf_counter() - start) * 1000, 2)
        res = {"status": "saved", "memory": record}
        preview = (note[:45] + "…") if len(note) > 45 else note
        get_telemetry().record_event(
            event_type="tool_call",
            level="success",
            tool="write_coach_note",
            arguments={"note": note, "category": category, "tags": tags, "tick": tick, "linked_ticks": linked_ticks, "cat_id": cat_id},
            duration_ms=dur,
            status="ok",
            summary=f"write_coach_note ({dur}ms) [{category}] [{cat_id or 'cat-1'}] \"{preview}\"",
            details={"arguments": {"note": note, "category": category, "tags": tags, "tick": tick, "linked_ticks": linked_ticks, "cat_id": cat_id}, "result": res},
        )
        return res

    @server.tool(
        name="read_coach_notes",
        description="Searches and retrieves historical entries from the Coach's tactical notebook (coach_memory.jsonl) across evaluation cycles. Supports filtering by cat_id ('all' by default, 'cat-1', or 'cat-2').",
    )
    async def read_coach_notes(
        limit: Optional[int] = 10,
        category: Optional[str] = None,
        search: Optional[str] = None,
        cat_id: Optional[str] = "all",
    ) -> Dict[str, Any]:
        """Recall saved coach memories with optional filtering."""
        start = time.perf_counter()
        memories = store.read_coach_memories(
            limit=limit, category=category, search=search, agent_id=cat_id
        )
        dur = round((time.perf_counter() - start) * 1000, 2)
        res = {
            "available": True,
            "cat_id": cat_id,
            "count": len(memories),
            "memories": memories,
        }
        cat_str = category if category else "all"
        target_agent = cat_id or "all"
        get_telemetry().record_event(
            event_type="tool_call",
            level="success",
            tool="read_coach_notes",
            arguments={"limit": limit, "category": category, "search": search, "cat_id": cat_id},
            duration_ms=dur,
            status="ok",
            summary=f"read_coach_notes ({dur}ms) [{target_agent}] -> {len(memories)} notes (category={cat_str})",
            details={"arguments": {"limit": limit, "category": category, "search": search, "cat_id": cat_id}, "result": res},
        )
        return res

    @server.tool(
        name="update_coach_note",
        description="Updates an existing coach memory note or its metadata by memory ID.",
    )
    async def update_coach_note(
        memory_id: str,
        note: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        linked_ticks: Optional[List[int]] = None,
        cat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Modify an existing coach memory note."""
        start = time.perf_counter()
        record = store.update_coach_memory(
            memory_id=memory_id,
            note=note,
            category=category,
            tags=tags,
            linked_ticks=linked_ticks,
            agent_id=cat_id,
        )
        dur = round((time.perf_counter() - start) * 1000, 2)
        if record is None:
            res = {"status": "not_found", "memory_id": memory_id}
            get_telemetry().record_event(
                event_type="tool_call",
                level="warn",
                tool="update_coach_note",
                arguments={"memory_id": memory_id, "note": note, "category": category, "tags": tags, "cat_id": cat_id},
                duration_ms=dur,
                status="warning",
                summary=f"update_coach_note ({dur}ms) -> memory_id {memory_id} not found",
                details={"arguments": {"memory_id": memory_id, "note": note, "category": category, "tags": tags, "cat_id": cat_id}, "result": res},
            )
            return res

        res = {"status": "updated", "memory": record}
        preview = (record.get("note", "")[:45] + "…") if len(record.get("note", "")) > 45 else record.get("note", "")
        cat_tag = record.get("cat_id") or cat_id or "auto"
        get_telemetry().record_event(
            event_type="tool_call",
            level="success",
            tool="update_coach_note",
            arguments={"memory_id": memory_id, "note": note, "category": category, "tags": tags, "cat_id": cat_id},
            duration_ms=dur,
            status="ok",
            summary=f"update_coach_note ({dur}ms) [{memory_id}] [{cat_tag}] \"{preview}\"",
            details={"arguments": {"memory_id": memory_id, "note": note, "category": category, "tags": tags, "cat_id": cat_id}, "result": res},
        )
        return res

    @server.tool(
        name="delete_coach_note",
        description="Deletes a coach tactical memory note by memory ID.",
    )
    async def delete_coach_note(memory_id: str, cat_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete an existing coach memory note."""
        start = time.perf_counter()
        deleted = store.delete_coach_memory(memory_id=memory_id, agent_id=cat_id)
        dur = round((time.perf_counter() - start) * 1000, 2)
        res = {"status": "deleted" if deleted else "not_found", "memory_id": memory_id}
        level = "success" if deleted else "warn"
        status_str = "ok" if deleted else "warning"
        cat_tag = cat_id or "auto"
        get_telemetry().record_event(
            event_type="tool_call",
            level=level,
            tool="delete_coach_note",
            arguments={"memory_id": memory_id, "cat_id": cat_id},
            duration_ms=dur,
            status=status_str,
            summary=f"delete_coach_note ({dur}ms) [{cat_tag}] -> {memory_id} ({'deleted' if deleted else 'not found'})",
            details={"arguments": {"memory_id": memory_id, "cat_id": cat_id}, "result": res},
        )
        return res

    @server.tool(
        name="pull_transcript",
        description="Pulls recent or windowed Cat agent transcript records (positions, facing, visibility, moves, reasoning) on demand.",
    )
    async def pull_transcript(
        last_n: Optional[int] = 50,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        cat_id: Optional[str] = "cat-1",
    ) -> Dict[str, Any]:
        """Fetches windowed transcript records formatted without absolute tick indices."""
        start = time.perf_counter()
        records = store.read_transcript_records(agent_id=cat_id)
        total_ticks = len(records)

        # Slice records according to parameters
        if offset is not None or limit is not None:
            off = offset if offset is not None else 0
            lim = limit if limit is not None else 50
            sliced = records[off : off + lim]
        elif last_n is not None:
            sliced = records[-last_n:] if last_n > 0 else []
        else:
            sliced = records[-50:]

        rendered = store.format_transcript(sliced)
        dur = round((time.perf_counter() - start) * 1000, 2)
        res = {
            "available": True,
            "total_ticks": total_ticks,
            "returned_ticks": len(sliced),
            "transcript": rendered,
        }
        get_telemetry().record_event(
            event_type="tool_call",
            level="success",
            tool="pull_transcript",
            arguments={"last_n": last_n, "limit": limit, "offset": offset, "cat_id": cat_id},
            duration_ms=dur,
            status="ok",
            summary=f"pull_transcript ({dur}ms) [{cat_id or 'cat-1'}] -> {len(sliced)}/{total_ticks} ticks",
            details={"arguments": {"last_n": last_n, "limit": limit, "offset": offset, "cat_id": cat_id}, "result": res},
        )
        return res

    @server.tool(
        name="get_notice_board",
        description="Reads the global Arena Notice Board, active game rules (including Kill Absorption), and system announcements broadcast to all agents.",
    )
    async def get_notice_board(sim_id: str = "active") -> Dict[str, Any]:
        """Fetch notice board and active rules for the simulation."""
        start = time.perf_counter()
        notice_data, bulletin = await _build_notice_board_data(sim_id=sim_id)
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="tool_call",
            level="success" if notice_data["available"] else "warn",
            tool="get_notice_board",
            arguments={"sim_id": sim_id},
            duration_ms=dur,
            status="ok" if notice_data["available"] else "warning",
            summary=f"get_notice_board ({dur}ms) -> absorption={'ACTIVE' if notice_data['active_modifiers']['absorption']['enabled'] else 'INACTIVE'} (tick {notice_data['tick']})",
            details={"arguments": {"sim_id": sim_id}, "result": notice_data},
        )
        return notice_data

    @server.tool(
        name="toggle_game_rule",
        description="Toggles or updates an authoritative game rule on the game server (e.g. 'absorption', 'heal_on_mouse_capture', 'heal_on_cat_elimination') and automatically updates the global notice board for all agents.",
    )
    async def toggle_game_rule(
        rule: str = "absorption",
        enabled: Any = True,
        sim_id: str = "active",
    ) -> Dict[str, Any]:
        """Toggle an active game rule on the game server and broadcast notice."""
        start = time.perf_counter()
        payload = {rule: enabled}
        ok, res, err = await _fetch_from_game_server(
            f"/api/simulations/{sim_id}/species/cat",
            method="PATCH",
            json_body=payload,
        )
        dur = round((time.perf_counter() - start) * 1000, 2)
        if not ok:
            return {"status": "error", "reason": err}

        notice_data, bulletin = await _build_notice_board_data(sim_id=sim_id)
        summary_msg = f"📢 ARENA RULE CHANGED: {rule} set to {enabled} (Tick {notice_data['tick']})"
        get_telemetry().record_event(
            event_type="notice_board",
            level="info",
            tool="toggle_game_rule",
            arguments={"rule": rule, "enabled": enabled, "sim_id": sim_id},
            duration_ms=dur,
            status="ok",
            summary=summary_msg,
            details={"rule": rule, "enabled": enabled, "notice_board": notice_data},
        )
        return {"status": "updated", "rule": rule, "enabled": enabled, "notice_board": notice_data}

    @server.tool(
        name="get_comms_log",
        description="Retrieve the rolling inter-cat radio communications and tactical chirps broadcast between cats.",
    )
    async def get_comms_log(limit: int = 50, sim_id: str = "active") -> Dict[str, Any]:
        start = time.perf_counter()
        ok, res, err = await _fetch_from_game_server(
            f"/api/simulations/{sim_id}/comms",
            params={"limit": limit},
        )
        dur = round((time.perf_counter() - start) * 1000, 2)
        if not ok:
            get_telemetry().record_event(
                event_type="tool_call",
                level="error",
                tool="get_comms_log",
                arguments={"limit": limit, "sim_id": sim_id},
                duration_ms=dur,
                status="error",
                summary=f"Failed to fetch comms log: {err}",
            )
            return {"status": "error", "reason": err, "transmissions": []}

        transmissions = res.get("transmissions", []) if isinstance(res, dict) else []
        get_telemetry().record_event(
            event_type="tool_call",
            level="info",
            tool="get_comms_log",
            arguments={"limit": limit, "sim_id": sim_id},
            duration_ms=dur,
            status="ok",
            summary=f"Retrieved {len(transmissions)} radio transmissions in {dur}ms",
            details={"count": len(transmissions)},
        )
        return {"status": "ok", "count": len(transmissions), "transmissions": transmissions}

    @server.tool(
        name="get_coach_payload",
        description="Inspect the latest prompt and payload sent to the coach / evaluator (System 2 Long-Term Consciousness), including all prompt sections, standing orders, and telemetry.",
    )
    async def get_coach_payload_tool(cat_id: str = "cat-1", tick: Optional[int] = None) -> Dict[str, Any]:
        start = time.perf_counter()
        record = store.read_coach_prompt_for_tick(tick=tick, agent_id=cat_id)
        dur = round((time.perf_counter() - start) * 1000, 2)
        if record:
            get_telemetry().record_event(
                event_type="tool_call",
                level="info",
                tool="get_coach_payload",
                arguments={"cat_id": cat_id, "tick": tick},
                duration_ms=dur,
                status="ok",
                summary=f"Retrieved coach payload for {cat_id} in {dur}ms",
                details={"model": record.get("model"), "agent_id": cat_id},
            )
            return {"status": "ok", "cat_id": cat_id, "record": record}
        return {"status": "not_found", "cat_id": cat_id, "detail": "No coach prompt recorded yet."}

    @server.tool(
        name="get_actor_payload",
        description="Inspect the prompt and payload sent to the tactical cat actor (System 1 fast brain), including all prompt sections and observations.",
    )
    async def get_actor_payload_tool(cat_id: str = "cat-1", tick: Optional[int] = None) -> Dict[str, Any]:
        start = time.perf_counter()
        if tick is not None:
            record = store.read_prompt_for_tick(tick)
        else:
            paths = store.get_cat_paths(cat_id)
            p = paths.get("prompts", store.PROMPTS_PATH)
            record = None
            if p.exists():
                try:
                    with p.open("r", encoding="utf-8") as fh:
                        lines = [ln.strip() for ln in fh if ln.strip()]
                        if lines:
                            record = json.loads(lines[-1])
                except Exception:
                    pass
        dur = round((time.perf_counter() - start) * 1000, 2)
        if record:
            return {"status": "ok", "cat_id": cat_id, "record": record}
        return {"status": "not_found", "cat_id": cat_id, "detail": "No actor prompt recorded yet."}


    # --------------------------------------------------------------------------
    # Resources
    # --------------------------------------------------------------------------

    @server.resource(
        "cm://sim/active/boxscore",
        name="Active Game Boxscore",
        description="Live box score KPIs for the active game simulation.",
        mime_type="application/json",
    )
    async def boxscore_resource() -> str:
        start = time.perf_counter()
        data = await get_boxscore(sim_id="active")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://sim/active/boxscore",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://sim/active/boxscore in {dur}ms",
            details={"uri": "cm://sim/active/boxscore"},
        )
        return json.dumps(data)

    @server.resource(
        "cm://sim/active/events",
        name="Active Game Events",
        description="Recent combat and game events from the active simulation.",
        mime_type="application/json",
    )
    async def events_resource() -> str:
        start = time.perf_counter()
        data = await get_event_reel(limit=50, sim_id="active")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://sim/active/events",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://sim/active/events in {dur}ms",
            details={"uri": "cm://sim/active/events"},
        )
        return json.dumps(data)

    @server.resource(
        "cm://sim/active/coverage_map",
        name="Active Coverage Map",
        description="Spatial exploration heatmap for the active simulation.",
        mime_type="application/json",
    )
    async def coverage_map_resource() -> str:
        start = time.perf_counter()
        data = await get_coverage_map(sim_id="active")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://sim/active/coverage_map",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://sim/active/coverage_map in {dur}ms",
            details={"uri": "cm://sim/active/coverage_map"},
        )
        return json.dumps(data)

    @server.resource(
        "cm://sim/active/comms",
        name="Active Inter-Cat Radio Comms",
        description="Rolling inter-cat radio transmissions and peer tactical chirps for the active game.",
        mime_type="application/json",
    )
    async def comms_resource() -> str:
        start = time.perf_counter()
        data = await get_comms_log(limit=50, sim_id="active")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://sim/active/comms",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://sim/active/comms in {dur}ms",
            details={"uri": "cm://sim/active/comms"},
        )
        return json.dumps(data)

    @server.resource(
        "cm://coach/notes",
        name="Coach Past Notes and Memory",
        description="The coach's historical tactical notes, hypotheses, and memories across all active cats.",
        mime_type="application/json",
    )
    async def coach_notes_resource() -> str:
        start = time.perf_counter()
        memories = store.read_coach_memories(limit=50, agent_id="all")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://coach/notes",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://coach/notes in {dur}ms ({len(memories)} notes)",
            details={"uri": "cm://coach/notes", "count": len(memories)},
        )
        return json.dumps({"count": len(memories), "memories": memories})

    @server.resource(
        "cm://coach/cat-1/notes",
        name="Cat-1 Coach Notes and Memory",
        description="Coach's private tactical notes and memories specifically for Cat-1.",
        mime_type="application/json",
    )
    async def coach_notes_cat1_resource() -> str:
        start = time.perf_counter()
        memories = store.read_coach_memories(limit=50, agent_id="cat-1")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://coach/cat-1/notes",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://coach/cat-1/notes in {dur}ms ({len(memories)} notes)",
            details={"uri": "cm://coach/cat-1/notes", "count": len(memories)},
        )
        return json.dumps({"count": len(memories), "cat_id": "cat-1", "memories": memories})

    @server.resource(
        "cm://coach/cat-2/notes",
        name="Cat-2 Coach Notes and Memory",
        description="Coach's private tactical notes and memories specifically for Cat-2.",
        mime_type="application/json",
    )
    async def coach_notes_cat2_resource() -> str:
        start = time.perf_counter()
        memories = store.read_coach_memories(limit=50, agent_id="cat-2")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://coach/cat-2/notes",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://coach/cat-2/notes in {dur}ms ({len(memories)} notes)",
            details={"uri": "cm://coach/cat-2/notes", "count": len(memories)},
        )
        return json.dumps({"count": len(memories), "cat_id": "cat-2", "memories": memories})

    @server.resource(
        "cm://game/notice_board",
        name="Global Arena Notice Board",
        description="Active arena rules, modifiers, and global bulletins broadcast to all agents.",
        mime_type="application/json",
    )
    async def notice_board_resource() -> str:
        start = time.perf_counter()
        notice_data, _ = await _build_notice_board_data(sim_id="active")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://game/notice_board",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://game/notice_board in {dur}ms",
            details={"uri": "cm://game/notice_board"},
        )
        return json.dumps(notice_data)

    @server.resource(
        "cm://game/rules",
        name="Active Game Rules",
        description="Active game rules and parameters from the authoritative game server.",
        mime_type="application/json",
    )
    async def rules_resource() -> str:
        start = time.perf_counter()
        notice_data, _ = await _build_notice_board_data(sim_id="active")
        dur = round((time.perf_counter() - start) * 1000, 2)
        get_telemetry().record_event(
            event_type="resource_read",
            level="info",
            resource="cm://game/rules",
            duration_ms=dur,
            status="ok",
            summary=f"Resource read cm://game/rules in {dur}ms",
            details={"uri": "cm://game/rules"},
        )
        return json.dumps(notice_data.get("active_modifiers", {}))

    return server


class MCPTelemetryMiddleware:
    """ASGI middleware to capture incoming connections, HTTP requests, and client metadata."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # Skip logging internal UI polling & static stream requests to keep logs clear
        if path == "/" or path.startswith("/static") or path in (
            "/api/mcp/stream",
            "/api/mcp/status",
            "/api/mcp/history",
        ):
            return await self.app(scope, receive, send)

        client = scope.get("client")
        client_ip = f"{client[0]}:{client[1]}" if client else "127.0.0.1"
        headers = dict(scope.get("headers", []))
        user_agent = headers.get(b"user-agent", b"").decode("latin1", errors="replace")
        telemetry = get_telemetry()

        if path == "/sse":
            telemetry.incr_active_clients()
            telemetry.record_event(
                event_type="connection",
                level="info",
                client_ip=client_ip,
                user_agent=user_agent,
                status="connected",
                summary=f"SSE Client Connected: {client_ip}",
                details={"user_agent": user_agent, "path": path, "method": method},
            )
            try:
                return await self.app(scope, receive, send)
            finally:
                telemetry.decr_active_clients()
                telemetry.record_event(
                    event_type="connection",
                    level="info",
                    client_ip=client_ip,
                    user_agent=user_agent,
                    status="disconnected",
                    summary=f"SSE Client Disconnected: {client_ip}",
                    details={"path": path},
                )

        if path.startswith("/messages"):
            telemetry.record_event(
                event_type="request",
                level="info",
                client_ip=client_ip,
                user_agent=user_agent,
                status="received",
                summary=f"Incoming JSON-RPC from {client_ip}",
                details={"path": path, "user_agent": user_agent},
            )
            return await self.app(scope, receive, send)

        return await self.app(scope, receive, send)


def create_mcp_app(server: Optional[MCPServer] = None):
    """Creates a Starlette ASGI app providing SSE, messages, and the dashboard UI."""
    if server is None:
        server = create_mcp_server()

    from mcp.server.transport_security import TransportSecuritySettings

    app = server.sse_app(
        sse_path="/sse",
        message_path="/messages/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    # 1. Dashboard Root HTML
    async def dashboard_endpoint(request: Request) -> Response:
        if INDEX_HTML_PATH.exists():
            return HTMLResponse(
                INDEX_HTML_PATH.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return HTMLResponse("<h1>Cat &amp; Mouse MCP Server</h1><p>index.html not found</p>")

    # 2. Real-Time Telemetry Stream via SSE
    async def stream_endpoint(request: Request) -> Response:
        telemetry = get_telemetry()

        async def sse_generator():
            init_payload = {
                "type": "init",
                "history": telemetry.get_history(limit=150),
                "stats": telemetry.get_stats(),
            }
            yield f"event: init\ndata: {json.dumps(init_payload)}\n\n"

            q = telemetry.subscribe()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        evt = await asyncio.wait_for(q.get(), timeout=2.0)
                        yield f"event: message\ndata: {json.dumps(evt)}\n\n"
                    except asyncio.TimeoutError:
                        yield f"event: ping\ndata: {json.dumps({'time': time.time()})}\n\n"
                    except asyncio.CancelledError:
                        break
            finally:
                telemetry.unsubscribe(q)

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 3. Server Health & Metrics
    async def status_endpoint(request: Request) -> JSONResponse:
        telemetry = get_telemetry()
        return JSONResponse({
            "server": "cat-and-mouse-mcp",
            "version": "0.1.0",
            "game_server_url": get_game_server_url(),
            "stats": telemetry.get_stats(),
        })

    # 4. History log retrieval
    async def history_endpoint(request: Request) -> JSONResponse:
        telemetry = get_telemetry()
        limit = int(request.query_params.get("limit", 100))
        event_type = request.query_params.get("type")
        return JSONResponse({
            "events": telemetry.get_history(limit=limit, event_type=event_type),
            "stats": telemetry.get_stats(),
        })

    # 5. Interactive Tool Execution endpoint for testing from the UI
    async def execute_tool_endpoint(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

        tool_name = body.get("tool")
        arguments = body.get("arguments", {})
        if not tool_name:
            return JSONResponse({"ok": False, "error": "Tool name is required"}, status_code=400)

        try:
            res = await server.call_tool(tool_name, arguments)
            if hasattr(res, "structured_content") and res.structured_content:
                result_payload = res.structured_content.get("result", res.structured_content)
            elif hasattr(res, "content"):
                texts = [getattr(c, "text", str(c)) for c in res.content]
                try:
                    result_payload = json.loads("\n".join(texts))
                except Exception:
                    result_payload = "\n".join(texts)
            else:
                result_payload = str(res)
            return JSONResponse({"ok": True, "tool": tool_name, "result": result_payload})
        except Exception as exc:
            return JSONResponse({"ok": False, "tool": tool_name, "error": str(exc)}, status_code=500)

    # 6. Clear Logs
    async def clear_logs_endpoint(request: Request) -> JSONResponse:
        telemetry = get_telemetry()
        telemetry.clear_history()
        return JSONResponse({"ok": True, "status": "cleared"})

    # 7. Coach Notes / Memories REST API
    async def coach_notes_list_endpoint(request: Request) -> JSONResponse:
        category = request.query_params.get("category") or None
        search = request.query_params.get("search") or None
        cat_id = request.query_params.get("cat_id") or request.query_params.get("agent_id") or "all"
        limit_str = request.query_params.get("limit")
        limit = int(limit_str) if limit_str and limit_str.isdigit() else None
        memories = store.read_coach_memories(limit=limit, category=category, search=search, agent_id=cat_id)

        all_for_counts = store.read_coach_memories(limit=500, agent_id="all")
        known_cats = store.list_known_cat_ids()
        cat_set = set(["cat-1", "cat-2", "cat-3", "evil-cat"])
        cat_set.update(known_cats)
        for m in all_for_counts:
            c = m.get("cat_id") or m.get("agent_id")
            if c:
                cat_set.add(c)

        counts = {"all": len(all_for_counts)}
        standing_notes = {}
        for c in cat_set:
            counts[c] = sum(1 for m in all_for_counts if m.get("cat_id") == c or m.get("agent_id") == c)
            standing_notes[c] = store.read_coaching_notes(agent_id=c)

        # Alias sync: if cat-3 is queried and empty, try evil-cat; if evil-cat queried and empty, try cat-3
        if not standing_notes.get("cat-3") and standing_notes.get("evil-cat"):
            standing_notes["cat-3"] = standing_notes["evil-cat"]
        if not standing_notes.get("evil-cat") and standing_notes.get("cat-3"):
            standing_notes["evil-cat"] = standing_notes["cat-3"]

        if counts.get("evil-cat") and not counts.get("cat-3"):
            counts["cat-3"] = counts["evil-cat"]
        elif counts.get("cat-3") and not counts.get("evil-cat"):
            counts["evil-cat"] = counts["cat-3"]

        active_notes = standing_notes.get(cat_id) or (standing_notes.get("cat-3") if cat_id in ("cat-3", "evil-cat") else None) or store.read_coaching_notes(agent_id=cat_id) or store.read_coaching_notes()

        return JSONResponse({
            "ok": True,
            "cat_id": cat_id,
            "count": len(memories),
            "counts": counts,
            "memories": memories,
            "active_standing_notes": active_notes,
            "standing_notes": standing_notes,
        })

    async def coach_notes_create_endpoint(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)
        note = body.get("note", "").strip()
        if not note:
            return JSONResponse({"ok": False, "error": "Note cannot be empty"}, status_code=400)
        category = body.get("category", "tactical")
        tags = body.get("tags")
        tick = body.get("tick")
        linked_ticks = body.get("linked_ticks")
        cat_id = body.get("cat_id") or body.get("agent_id") or "cat-1"
        record = store.append_coach_memory(
            note=note, category=category, tags=tags, tick=tick, linked_ticks=linked_ticks, agent_id=cat_id
        )
        return JSONResponse({"ok": True, "memory": record})

    async def coach_notes_item_endpoint(request: Request) -> JSONResponse:
        memory_id = request.path_params.get("id", "")
        cat_id = request.query_params.get("cat_id") or None
        if request.method == "PUT":
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)
            if not cat_id:
                cat_id = body.get("cat_id") or body.get("agent_id")
            record = store.update_coach_memory(
                memory_id=memory_id,
                note=body.get("note"),
                category=body.get("category"),
                tags=body.get("tags"),
                linked_ticks=body.get("linked_ticks"),
                agent_id=cat_id,
            )
            if record is None:
                return JSONResponse({"ok": False, "error": "Note not found"}, status_code=404)
            return JSONResponse({"ok": True, "memory": record})
        elif request.method == "DELETE":
            deleted = store.delete_coach_memory(memory_id=memory_id, agent_id=cat_id)
            if not deleted:
                return JSONResponse({"ok": False, "error": "Note not found"}, status_code=404)
            return JSONResponse({"ok": True, "deleted": True, "id": memory_id})
        return JSONResponse({"ok": False, "error": "Method not allowed"}, status_code=405)

    async def coach_standing_notes_endpoint(request: Request) -> JSONResponse:
        cat_id = request.query_params.get("cat_id") or "cat-1"
        if request.method in ("POST", "PUT"):
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)
            notes_text = body.get("notes", "")
            target_cat = body.get("cat_id") or cat_id
            store.write_coaching_notes(notes_text, agent_id=target_cat)
            standing = {
                "cat-1": store.read_coaching_notes(agent_id="cat-1"),
                "cat-2": store.read_coaching_notes(agent_id="cat-2"),
                "cat-3": store.read_coaching_notes(agent_id="cat-3") or store.read_coaching_notes(agent_id="evil-cat"),
                "evil-cat": store.read_coaching_notes(agent_id="evil-cat") or store.read_coaching_notes(agent_id="cat-3"),
            }
            return JSONResponse({
                "ok": True,
                "cat_id": target_cat,
                "notes": store.read_coaching_notes(agent_id=target_cat),
                "standing_notes": standing,
            })
        standing = {
            "cat-1": store.read_coaching_notes(agent_id="cat-1"),
            "cat-2": store.read_coaching_notes(agent_id="cat-2"),
            "cat-3": store.read_coaching_notes(agent_id="cat-3") or store.read_coaching_notes(agent_id="evil-cat"),
            "evil-cat": store.read_coaching_notes(agent_id="evil-cat") or store.read_coaching_notes(agent_id="cat-3"),
        }
        return JSONResponse({
            "ok": True,
            "cat_id": cat_id,
            "notes": store.read_coaching_notes(agent_id=cat_id),
            "standing_notes": standing,
        })

    # Coach Payload Inspection endpoint
    async def coach_payload_endpoint(request: Request) -> JSONResponse:
        cat_id = request.query_params.get("cat_id") or request.query_params.get("agent_id") or "cat-1"
        tick_param = request.query_params.get("tick")
        tick = int(tick_param) if tick_param and tick_param.isdigit() else None
        record = store.read_coach_prompt_for_tick(tick=tick, agent_id=cat_id)
        if record:
            return JSONResponse({"ok": True, "status": "ok", "cat_id": cat_id, "tick": tick, "record": record})
        return JSONResponse({
            "ok": False,
            "status": "not_found",
            "cat_id": cat_id,
            "tick": tick,
            "record": None,
            "detail": f"No coach prompt recorded yet for {cat_id}.",
        }, status_code=404)

    # 8. Notice Board REST API
    async def notice_board_endpoint(request: Request) -> JSONResponse:
        sim_id = request.query_params.get("sim_id", "active")
        notice_data, bulletin = await _build_notice_board_data(sim_id=sim_id)
        return JSONResponse({"ok": True, "notice_board": notice_data})

    async def notice_board_toggle_endpoint(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)
        rule = body.get("rule", "absorption")
        enabled = bool(body.get("enabled", True))
        sim_id = body.get("sim_id", "active")

        ok, res, err = await _fetch_from_game_server(
            f"/api/simulations/{sim_id}/species/cat",
            method="PATCH",
            json_body={rule: enabled},
        )
        if not ok:
            return JSONResponse({"ok": False, "error": err}, status_code=500)

        notice_data, bulletin = await _build_notice_board_data(sim_id=sim_id)
        get_telemetry().record_event(
            event_type="notice_board",
            level="info",
            status="ok",
            summary=f"📢 NOTICE BOARD TOGGLE: {rule} -> {enabled} (Tick {notice_data['tick']})",
            details={"rule": rule, "enabled": enabled, "notice_board": notice_data},
        )
        return JSONResponse({"ok": True, "notice_board": notice_data})

    # 9. Inter-Cat Radio Comms REST API
    async def comms_endpoint(request: Request) -> JSONResponse:
        sim_id = request.query_params.get("sim_id", "active")
        limit_val = int(request.query_params.get("limit", 50))
        ok, res, err = await _fetch_from_game_server(
            f"/api/simulations/{sim_id}/comms",
            params={"limit": limit_val},
        )
        if not ok:
            return JSONResponse({"ok": False, "transmissions": [], "error": err})
        transmissions = res.get("transmissions", []) if isinstance(res, dict) else []
        return JSONResponse({"ok": True, "transmissions": transmissions})

    # 10. Coach Live Chat REST API & Unified Game Chat Monitor
    async def coach_chat_list_endpoint(request: Request) -> JSONResponse:
        cat_id = request.query_params.get("cat_id") or "all"
        view_filter = (request.query_params.get("view") or "all").lower()
        sender_filter = request.query_params.get("from") or request.query_params.get("sender") or None
        recipient_filter = request.query_params.get("to") or request.query_params.get("recipient") or None
        source_filter = request.query_params.get("source") or None
        search_query = (request.query_params.get("search") or "").strip().lower()
        limit_val = int(request.query_params.get("limit", 200))

        all_messages: List[Dict[str, Any]] = []

        # Dynamic roster discovery: seeds with known cats and store runtimes
        discovered_roster = {"cat-1", "cat-2", "cat-3", "evil-cat"}
        try:
            from actors.cat import store as cat_store
            for cid in cat_store.list_known_cat_ids():
                if cid and cid not in ("all", "*"):
                    discovered_roster.add(cid)
        except Exception:
            pass

        # 1. Fetch inter-cat radio transmissions & DMs from game server engine
        try:
            ok, comms_payload, _ = await _fetch_from_game_server("/api/simulations/active/comms?limit=200")
            if ok and isinstance(comms_payload, dict):
                for t in comms_payload.get("transmissions", []):
                    rec = t.get("recipient_id") or "*"
                    snd = t.get("sender_id") or "cat"
                    if snd and snd not in ("*", "broadcast", "all", "general", "commander"):
                        discovered_roster.add(snd)
                    if rec and rec not in ("*", "broadcast", "all", "general", "commander"):
                        discovered_roster.add(rec)
                    is_dm = rec not in ("*", "broadcast", "all")
                    is_coach = "coach" in str(snd).lower() or "coach" in str(rec).lower()
                    # Determine channel
                    if is_coach:
                        ch = "coach_a2a"
                    elif is_dm:
                        ch = "cat_dm"
                    else:
                        ch = "cat_broadcast"

                    all_messages.append({
                        "id": t.get("message_id") or f"comm-{t.get('tick')}-{snd}-{rec}",
                        "cat_id": snd,
                        "sender": snd,
                        "recipient_id": rec,
                        "message": t.get("message", ""),
                        "timestamp": t.get("timestamp") or f"Tick #{t.get('tick', 0)}",
                        "tick": t.get("tick"),
                        "round": t.get("round"),
                        "source": "cat_radio",
                        "channel": ch,
                        "is_dm": is_dm,
                        "position": t.get("position"),
                    })
        except Exception:
            pass

        roster = sorted(list(discovered_roster))

        # 2. Discover cat runtime endpoints / ports for Coach & Commander chats
        # 2. Discover cat runtime endpoints / ports for Coach & Commander chats
        cat_endpoints = {
            "cat-1": os.environ.get("CAT_1_URL", "http://127.0.0.1:8001"),
            "cat-2": os.environ.get("CAT_2_URL", "http://127.0.0.1:8003"),
            "cat-3": os.environ.get("CAT_3_URL") or os.environ.get("EVIL_CAT_URL", "http://127.0.0.1:8005"),
            "evil-cat": os.environ.get("EVIL_CAT_URL", "http://127.0.0.1:8005"),
            "cat-evil": os.environ.get("EVIL_CAT_URL", "http://127.0.0.1:8005"),
        }
        # Gather across all roster agents so filtering and counting are complete and accurate
        async with httpx.AsyncClient(timeout=1.5) as client:
            for cid in roster:
                base_url = cat_endpoints.get(cid)
                if not base_url:
                    continue
                # General <-> Coach radio chat history
                try:
                    resp = await client.get(f"{base_url}/api/agent/general/chat-history")
                    if resp.status_code == 200:
                        msgs = resp.json()
                        for m in msgs:
                            entry = dict(m)
                            entry["cat_id"] = cid
                            snd = entry.get("sender", "")
                            is_gen = entry.get("source") == "user" or snd == "general"
                            # Keep General<->Coach conversation strictly in 'general' channel
                            entry["channel"] = "general"
                            entry["sender"] = "general" if is_gen else "coach"
                            entry["recipient_id"] = f"coach({cid})" if is_gen else "general"
                            entry["is_dm"] = False
                            all_messages.append(entry)
                except Exception:
                    pass

                # Commander <-> Cat radio chat history
                try:
                    resp_cmd = await client.get(f"{base_url}/api/agent/chat-history")
                    if resp_cmd.status_code == 200:
                        msgs = resp_cmd.json()
                        for m in msgs:
                            entry = dict(m)
                            entry["cat_id"] = cid
                            snd = entry.get("sender", "")
                            is_cmd = entry.get("source") == "user" or snd == "commander"
                            entry["channel"] = "commander"
                            entry["sender"] = "commander" if is_cmd else cid
                            entry["recipient_id"] = cid if is_cmd else "commander"
                            entry["is_dm"] = False
                            all_messages.append(entry)
                except Exception:
                    pass

        # 3. Merge A2A Coach diplomatic inbox & outbox messages across all cats in roster
        try:
            from actors.a2a import store as a2a_store
            from actors.a2a.store import normalize_agent_id
            for cid in roster:
                inbox_msgs = a2a_store.get_coach_inbox(cid, limit=100)
                outbox_msgs = a2a_store.get_outbox(cid, limit=100)
                combined = []
                seen_im_ids = set()
                for im in (inbox_msgs + outbox_msgs):
                    imid = im.get("message_id") or f"{im.get('timestamp')}-{im.get('content')}"
                    if imid in seen_im_ids:
                        continue
                    seen_im_ids.add(imid)
                    combined.append(im)

                for im in combined:
                    snd = im.get("sender_id", "peer_coach")
                    rec = im.get("recipient_id", cid)
                    is_dm = rec not in ("*", "broadcast", "all")
                    is_coach_msg = "coach" in str(snd).lower() or "coach" in str(rec).lower()
                    ch = "coach_a2a" if is_coach_msg else ("cat_dm" if is_dm else "cat_broadcast")
                    all_messages.append({
                        "id": im.get("message_id") or f"a2a-{im.get('timestamp')}-{snd}-{rec}",
                        "cat_id": cid,
                        "sender": snd,
                        "recipient_id": rec,
                        "message": im.get("content", ""),
                        "timestamp": im.get("timestamp", ""),
                        "tick": im.get("tick"),
                        "source": "a2a_coach",
                        "channel": ch,
                        "is_dm": is_dm,
                        "message_type": im.get("message_type", "DIRECT_MESSAGE"),
                    })
        except Exception:
            pass

        # Deduplicate messages by unique identifier/signature across agents and sources
        seen_keys = set()
        deduped_messages: List[Dict[str, Any]] = []
        for m in all_messages:
            mid = m.get("id")
            if m.get("source") == "a2a_coach" and mid:
                key = f"a2a:{mid}"
            elif m.get("source") == "cat_radio" and mid:
                key = f"radio:{mid}"
            elif mid:
                key = f"{m.get('cat_id')}:{mid}"
            else:
                key = f"{m.get('cat_id')}:{m.get('sender')}:{m.get('message')}:{m.get('timestamp')}:{m.get('tick')}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped_messages.append(m)
        all_messages = deduped_messages

        # Helper to match an agent ID against a message (supporting 'cat-1' matching 'coach(cat-1)', aliases, etc.)
        from actors.a2a.store import normalize_agent_id
        def _cat_matches(msg: Dict[str, Any], query_id: str) -> bool:
            if not query_id or query_id in ("all", "*"):
                return True
            q_norm = normalize_agent_id(query_id)
            aliases = {query_id.lower(), q_norm}
            if q_norm == "cat-3":
                aliases.update(["evil-cat", "cat-evil"])
            elif q_norm in ("evil-cat", "cat-evil"):
                aliases.update(["cat-3"])

            # Check direct cat_id
            m_cid = str(msg.get("cat_id") or "").lower()
            if m_cid in aliases or normalize_agent_id(m_cid) in aliases:
                return True

            # Check sender
            m_snd = str(msg.get("sender") or "").lower()
            if m_snd in aliases or normalize_agent_id(m_snd) in aliases or any(a in m_snd for a in aliases if len(a) > 2):
                return True

            # Check recipient
            m_rec = str(msg.get("recipient_id") or "").lower()
            if m_rec in aliases or normalize_agent_id(m_rec) in aliases or any(a in m_rec for a in aliases if len(a) > 2):
                return True

            return False

        # Apply filtering
        filtered = all_messages

        # View filters
        if view_filter in ("dms", "cat_dms", "dms_only"):
            # Review cat1<->cat2 dms strictly without any coach!
            filtered = [
                m for m in filtered
                if m.get("channel") == "cat_dm"
                and "coach" not in str(m.get("sender", "")).lower()
                and "coach" not in str(m.get("recipient_id", "")).lower()
            ]
        elif view_filter in ("coach_a2a", "coach_coach", "peer_coach"):
            # Review coach1<->coach2 diplomatic comms strictly!
            filtered = [
                m for m in filtered
                if m.get("channel") == "coach_a2a"
                or ("coach" in str(m.get("sender", "")).lower() and "coach" in str(m.get("recipient_id", "")).lower())
            ]
        elif view_filter in ("broadcasts", "broadcasts_only"):
            filtered = [m for m in filtered if m.get("channel") == "cat_broadcast"]
        elif view_filter in ("no_coach", "exclude_coach"):
            filtered = [
                m for m in filtered
                if m.get("channel") not in ("coach", "coach_a2a")
                and "coach" not in str(m.get("sender", "")).lower()
                and "coach" not in str(m.get("recipient_id", "")).lower()
            ]
        elif view_filter in ("coach", "coach_only"):
            filtered = [m for m in filtered if m.get("channel") in ("coach", "coach_a2a")]
        elif view_filter in ("general", "general_only"):
            filtered = [m for m in filtered if m.get("channel") in ("general", "commander")]

        # Agent filter (supports cat-1 matching coach(cat-1), etc.)
        if cat_id not in ("all", "*"):
            filtered = [m for m in filtered if _cat_matches(m, cat_id)]

        # Additional explicit filters (From & To)
        if sender_filter and sender_filter not in ("all", "*"):
            s_low = sender_filter.lower()
            if s_low == "all_cats":
                filtered = [
                    m for m in filtered
                    if "coach" not in str(m.get("sender", "")).lower()
                    and str(m.get("sender", "")).lower() not in ("general", "commander")
                ]
            elif s_low == "all_coaches":
                filtered = [m for m in filtered if "coach" in str(m.get("sender", "")).lower()]
            else:
                s_norm = normalize_agent_id(s_low)
                s_aliases = {s_low, s_norm}
                if s_norm == "cat-3":
                    s_aliases.update(["evil-cat", "cat-evil"])
                elif s_norm in ("evil-cat", "cat-evil"):
                    s_aliases.update(["cat-3"])
                filtered = [
                    m for m in filtered
                    if str(m.get("sender", "")).lower() in s_aliases
                    or normalize_agent_id(m.get("sender")) in s_aliases
                    or any(a in str(m.get("sender", "")).lower() for a in s_aliases if len(a) > 2)
                ]

        if recipient_filter and recipient_filter not in ("all",):
            r_low = recipient_filter.lower()
            if r_low in ("*", "broadcast", "all_broadcasts"):
                filtered = [
                    m for m in filtered
                    if m.get("recipient_id") in ("*", "broadcast", "all") or not m.get("is_dm")
                ]
            elif r_low == "all_dms":
                filtered = [
                    m for m in filtered
                    if m.get("is_dm") or (m.get("recipient_id") and m.get("recipient_id") not in ("*", "broadcast", "all"))
                ]
            elif r_low == "all_coaches":
                filtered = [m for m in filtered if "coach" in str(m.get("recipient_id", "")).lower()]
            elif r_low == "all_cats":
                filtered = [
                    m for m in filtered
                    if "coach" not in str(m.get("recipient_id", "")).lower()
                    and str(m.get("recipient_id", "")).lower() not in ("general", "commander", "*", "broadcast", "all")
                ]
            else:
                r_norm = normalize_agent_id(r_low)
                r_aliases = {r_low, r_norm}
                if r_norm == "cat-3":
                    r_aliases.update(["evil-cat", "cat-evil"])
                elif r_norm in ("evil-cat", "cat-evil"):
                    r_aliases.update(["cat-3"])
                filtered = [
                    m for m in filtered
                    if str(m.get("recipient_id", "")).lower() in r_aliases
                    or normalize_agent_id(m.get("recipient_id")) in r_aliases
                    or any(a in str(m.get("recipient_id", "")).lower() for a in r_aliases if len(a) > 2)
                ]

        if source_filter:
            filtered = [m for m in filtered if m.get("source") == source_filter]
        if search_query:
            filtered = [
                m for m in filtered
                if search_query in str(m.get("message", "")).lower()
                or search_query in str(m.get("sender", "")).lower()
                or search_query in str(m.get("recipient_id", "")).lower()
                or search_query in str(m.get("tick", "")).lower()
            ]

        # Sort by timestamp / tick if available
        def _chat_sort_key(m):
            return (m.get("tick") or 0, str(m.get("timestamp", "")))

        filtered.sort(key=_chat_sort_key)
        if limit_val > 0:
            filtered = filtered[-limit_val:]

        counts = {
            "all": len(all_messages),
            "cat_dms": sum(
                1 for m in all_messages
                if m.get("channel") == "cat_dm"
                and "coach" not in str(m.get("sender", "")).lower()
                and "coach" not in str(m.get("recipient_id", "")).lower()
            ),
            "coach_a2a": sum(
                1 for m in all_messages
                if m.get("channel") == "coach_a2a"
                or ("coach" in str(m.get("sender", "")).lower() and "coach" in str(m.get("recipient_id", "")).lower())
            ),
            "broadcasts": sum(1 for m in all_messages if m.get("channel") == "cat_broadcast"),
            "no_coach": sum(
                1 for m in all_messages
                if m.get("channel") not in ("coach", "coach_a2a")
                and "coach" not in str(m.get("sender", "")).lower()
                and "coach" not in str(m.get("recipient_id", "")).lower()
            ),
            "coach": sum(1 for m in all_messages if m.get("channel") in ("coach", "coach_a2a")),
            "general": sum(1 for m in all_messages if m.get("channel") in ("general", "commander")),
            "roster": {},
        }
        for c in roster:
            c_count = sum(1 for m in all_messages if _cat_matches(m, c))
            counts[c] = c_count
            counts["roster"][c] = c_count

        return JSONResponse({
            "ok": True,
            "cat_id": cat_id,
            "from": sender_filter,
            "to": recipient_filter,
            "view": view_filter,
            "roster": roster,
            "total": len(filtered),
            "counts": counts,
            "messages": filtered,
        })

    # Dedicated Standalone Live Game Chat Monitor Page
    async def chat_page_endpoint(request: Request) -> Response:
        if CHAT_HTML_PATH.exists():
            return HTMLResponse(
                CHAT_HTML_PATH.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return HTMLResponse("<h1>Live Game Chat Monitor</h1><p>chat.html not found</p>")

    # Dedicated Standalone Coach Private Notes & Tactical Notebook Page
    async def notes_page_endpoint(request: Request) -> Response:
        if NOTES_HTML_PATH.exists():
            return HTMLResponse(
                NOTES_HTML_PATH.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return HTMLResponse("<h1>Coach Private Notes</h1><p>notes.html not found</p>")

    async def coach_chat_send_endpoint(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

        message = (body.get("message") or "").strip()
        cat_id = body.get("cat_id") or "cat-1"
        steer_directive = bool(body.get("steer_directive", False))

        if not message:
            return JSONResponse({"ok": False, "error": "Message cannot be empty"}, status_code=400)

        cat_endpoints = {
            "cat-1": os.environ.get("CAT_1_URL", "http://127.0.0.1:8001"),
            "cat-2": os.environ.get("CAT_2_URL", "http://127.0.0.1:8003"),
            "cat-3": os.environ.get("CAT_3_URL") or os.environ.get("EVIL_CAT_URL", "http://127.0.0.1:8005"),
            "evil-cat": os.environ.get("EVIL_CAT_URL", "http://127.0.0.1:8005"),
            "cat-evil": os.environ.get("EVIL_CAT_URL", "http://127.0.0.1:8005"),
        }
        base_url = cat_endpoints.get(cat_id, "http://127.0.0.1:8001")

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(
                    f"{base_url}/api/agent/general/prompt",
                    json={"prompt": message, "steer_directive": steer_directive},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return JSONResponse({"ok": True, "cat_id": cat_id, "result": data})
                else:
                    return JSONResponse({"ok": False, "error": f"Agent returned HTTP {resp.status_code}: {resp.text}"}, status_code=resp.status_code)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    # Register additional HTTP routes
    app.add_route("/", dashboard_endpoint, methods=["GET"])
    app.add_route("/chat", chat_page_endpoint, methods=["GET"])
    app.add_route("/notes", notes_page_endpoint, methods=["GET"])
    app.add_route("/api/mcp/stream", stream_endpoint, methods=["GET"])
    app.add_route("/api/mcp/status", status_endpoint, methods=["GET"])
    app.add_route("/api/mcp/history", history_endpoint, methods=["GET"])
    app.add_route("/api/mcp/execute-tool", execute_tool_endpoint, methods=["POST"])
    app.add_route("/api/mcp/clear-logs", clear_logs_endpoint, methods=["POST"])
    app.add_route("/api/mcp/notice-board", notice_board_endpoint, methods=["GET"])
    app.add_route("/api/mcp/notice-board/toggle", notice_board_toggle_endpoint, methods=["POST"])
    app.add_route("/api/mcp/comms", comms_endpoint, methods=["GET"])
    app.add_route("/api/mcp/coach-notes", coach_notes_list_endpoint, methods=["GET"])
    app.add_route("/api/mcp/coach-notes", coach_notes_create_endpoint, methods=["POST"])
    app.add_route("/api/mcp/coach-notes/{id}", coach_notes_item_endpoint, methods=["PUT", "DELETE"])
    app.add_route("/api/mcp/coach-standing-notes", coach_standing_notes_endpoint, methods=["GET", "POST", "PUT"])
    app.add_route("/api/mcp/coach-payload", coach_payload_endpoint, methods=["GET"])
    app.add_route("/api/mcp/coach-chat", coach_chat_list_endpoint, methods=["GET"])
    app.add_route("/api/mcp/game-chat", coach_chat_list_endpoint, methods=["GET"])
    app.add_route("/api/mcp/coach-chat/send", coach_chat_send_endpoint, methods=["POST"])

    return MCPTelemetryMiddleware(app)

