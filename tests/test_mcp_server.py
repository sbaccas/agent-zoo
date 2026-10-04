"""Tests for the Cat & Mouse Model Context Protocol (MCP) server, tools, and client."""

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import uvicorn

from app.mcp.client import MCPClient
from app.mcp.server import create_mcp_app, create_mcp_server
from actors.cat import store
from actors.cat.subagents import eval as ev


@pytest.fixture(autouse=True)
def clean_memory_store(tmp_path, monkeypatch):
    """Ensure tests run against an isolated runtime directory."""
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(store, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(store, "TRANSCRIPT_PATH", tmp_path / "transcript.jsonl")
    monkeypatch.setattr(store, "COACH_MEMORY_PATH", tmp_path / "coach_memory.jsonl")
    monkeypatch.setattr(store, "COACHING_NOTES_PATH", tmp_path / "coaching_notes.md")
    monkeypatch.setattr(store, "NOTICE_BOARD_PATH", tmp_path / "notice_board.md")
    store.reset_coach_memories()
    store.reset_notice_board()
    yield
    store.reset_coach_memories()
    store.reset_notice_board()


@pytest.mark.asyncio
async def test_mcp_server_registers_expected_tools():
    """Server exposes all game analytics and coach memory tools."""
    server = create_mcp_server()
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]

    expected = [
        "get_boxscore",
        "get_notice_board",
        "toggle_game_rule",
        "get_coverage_map",
        "get_event_reel",
        "pull_transcript",
        "write_coach_note",
        "read_coach_notes",
    ]
    for exp in expected:
        assert exp in tool_names, f"Missing expected tool {exp} in {tool_names}"


@pytest.mark.asyncio
async def test_mcp_server_registers_expected_resources():
    """Server exposes all standard cm:// resource URIs."""
    server = create_mcp_server()
    resources = await server.list_resources()
    uris = [str(r.uri) for r in resources]

    expected = [
        "cm://sim/active/boxscore",
        "cm://sim/active/events",
        "cm://sim/active/coverage_map",
        "cm://coach/notes",
        "cm://game/notice_board",
        "cm://game/rules",
    ]
    for exp in expected:
        assert exp in uris, f"Missing expected resource {exp} in {uris}"


@pytest.mark.asyncio
async def test_write_and_read_coach_notes_tools():
    """Coach memory tools properly save and retrieve notes with categorization."""
    server = create_mcp_server()

    # Write two notes in different categories
    res1 = await server.call_tool(
        "write_coach_note",
        {
            "note": "Cat-1 lunged into south wall at tick 120",
            "category": "tactical",
            "tags": ["south", "wall_bonk"],
            "tick": 120,
        },
    )
    assert res1.structured_content["result"]["status"] == "saved"
    assert res1.structured_content["result"]["memory"]["note"] == "Cat-1 lunged into south wall at tick 120"

    res2 = await server.call_tool(
        "write_coach_note",
        {
            "note": "Mice are clustering around east pantry",
            "category": "observation",
            "tags": ["east", "mice"],
            "tick": 140,
        },
    )
    assert res2.structured_content["result"]["status"] == "saved"

    # Read back all notes
    read_all = await server.call_tool("read_coach_notes", {"limit": 10})
    memories = read_all.structured_content["result"]["memories"]
    assert len(memories) == 2
    # Newest first
    assert memories[0]["note"] == "Mice are clustering around east pantry"
    assert memories[1]["note"] == "Cat-1 lunged into south wall at tick 120"

    # Read filtered by category
    read_tactical = await server.call_tool(
        "read_coach_notes", {"category": "tactical"}
    )
    assert read_tactical.structured_content["result"]["count"] == 1
    assert read_tactical.structured_content["result"]["memories"][0]["tags"] == ["south", "wall_bonk"]

    # Read filtered by search term
    read_search = await server.call_tool("read_coach_notes", {"search": "pantry"})
    assert read_search.structured_content["result"]["count"] == 1
    assert "east pantry" in read_search.structured_content["result"]["memories"][0]["note"]

    # Update note
    target_id = memories[0]["id"]
    upd_res = await server.call_tool(
        "update_coach_note",
        {
            "memory_id": target_id,
            "note": "Mice are clustering around east pantry and kitchen",
            "category": "hypothesis",
            "tags": ["east", "mice", "kitchen"],
        },
    )
    assert upd_res.structured_content["result"]["status"] == "updated"
    assert upd_res.structured_content["result"]["memory"]["note"] == "Mice are clustering around east pantry and kitchen"
    assert upd_res.structured_content["result"]["memory"]["category"] == "hypothesis"

    # Delete note
    del_res = await server.call_tool("delete_coach_note", {"memory_id": target_id})
    assert del_res.structured_content["result"]["status"] == "deleted"

    # Read back - should now have 1 left
    read_after = await server.call_tool("read_coach_notes", {"limit": 10})
    assert len(read_after.structured_content["result"]["memories"]) == 1



@pytest.mark.asyncio
async def test_get_event_reel_tool_with_mocked_backend():
    """get_event_reel tool passes parameters and returns formatted events."""
    server = create_mcp_server()

    mock_resp = httpx.Response(
        200,
        json={
            "simulation_id": "sim-test",
            "tick": 340,
            "round": 1,
            "events": [
                {
                    "tick": 335,
                    "event_type": "attack",
                    "actor_id": "cat-1",
                    "target_id": "mouse-3",
                    "details": {"hit": False, "reason": "whiff"},
                }
            ],
            "deaths": [],
            "entity_events": {"cat-1": [{"tick": 335, "event_type": "attack"}]},
        },
        request=httpx.Request("GET", "http://127.0.0.1:8000/api/simulations/active/events"),
    )

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        res = await server.call_tool(
            "get_event_reel",
            {"limit": 10, "since_tick": 300, "actor_id": "cat-1", "event_type": "attack"},
        )

    data = res.structured_content["result"]
    assert data["available"] is True
    assert data["tick"] == 340
    assert len(data["events"]) == 1
    assert data["events"][0]["event_type"] == "attack"
    assert data["events"][0]["details"]["reason"] == "whiff"


@pytest.mark.asyncio
async def test_read_coach_notes_resource():
    """Reading cm://coach/notes resource produces valid JSON list of memories."""
    server = create_mcp_server()

    store.append_coach_memory("Initial test note for resource", category="test")

    res = await server.read_resource("cm://coach/notes")
    assert len(res) == 1
    payload = json.loads(res[0].content)
    assert payload["count"] == 1
    assert payload["memories"][0]["note"] == "Initial test note for resource"


@pytest.mark.asyncio
async def test_mcp_server_client_over_sse():
    """End-to-end test of MCPClient connecting to MCPServer over real SSE transport."""
    app = create_mcp_app()
    test_port = 9058
    config = uvicorn.Config(app, host="127.0.0.1", port=test_port, log_level="error")
    srv = uvicorn.Server(config)

    thread = threading.Thread(target=srv.run)
    thread.daemon = True
    thread.start()

    # Wait for server to bind
    for _ in range(20):
        if srv.started:
            break
        await asyncio.sleep(0.05)

    try:
        client = MCPClient(base_url=f"http://127.0.0.1:{test_port}", timeout=3.0)

        # 1. List tools
        tools = await client.list_tools()
        names = [t["name"] for t in tools]
        assert "write_coach_note" in names
        assert "read_coach_notes" in names
        assert "get_event_reel" in names

        # 2. Call tool
        w_res = await client.call_tool(
            "write_coach_note",
            {"note": "SSE transport note", "category": "sse"},
        )
        assert w_res.get("status") == "saved"

        # 3. Read back
        r_res = await client.call_tool("read_coach_notes", {"limit": 5})
        assert r_res.get("count") >= 1
        assert any(m["note"] == "SSE transport note" for m in r_res["memories"])

        # 3b. Pull transcript
        t_res = await client.call_tool("pull_transcript", {"last_n": 10})
        assert t_res.get("available") is True
        assert "transcript" in t_res
        assert "total_ticks" in t_res

        # 4. List resources
        resources = await client.list_resources()
        uris = [r["uri"] for r in resources]
        assert "cm://coach/notes" in uris

        # 5. Read resource
        res_data = await client.read_resource("cm://coach/notes")
        assert isinstance(res_data, dict)
        assert res_data.get("count") >= 1
    finally:
        srv.should_exit = True
        thread.join(timeout=1.0)


def test_eval_agent_has_mcp_tools():
    """Coach evaluator agent is equipped with the new tools."""
    tool_names = [getattr(t, "__name__", None) or getattr(t, "name", None) for t in ev.eval_agent.tools]
    assert "get_boxscore" in tool_names
    assert "get_coverage_map" in tool_names
    assert "get_event_reel" in tool_names
    assert "pull_transcript" in tool_names
    assert "write_coach_note" in tool_names
    assert "read_coach_notes" in tool_names


def test_general_agent_has_mcp_tools():
    """General Radio coach agent is equipped with event reel and memory query tools."""
    tool_names = [getattr(t, "__name__", None) or getattr(t, "name", None) for t in ev.general_agent.tools]
    assert "get_boxscore" in tool_names
    assert "get_event_reel" in tool_names
    assert "pull_transcript" in tool_names
    assert "read_coach_notes" in tool_names


@pytest.mark.asyncio
async def test_adk_canonical_declarations_for_new_tools():
    """ADK successfully derives valid schema declarations for the new tools."""
    tools = await ev.eval_agent.canonical_tools()
    by_name = {t.name: t for t in tools}

    for expected_name in ["get_event_reel", "pull_transcript", "write_coach_note", "read_coach_notes"]:
        assert expected_name in by_name
        decl = by_name[expected_name]
        assert decl.description.strip(), f"{expected_name} is missing description"


def test_new_tools_do_not_contain_banned_tactical_words():
    """Tool docstrings comply with the data-only rule."""
    from tests.test_coach_boxscore_tool import BANNED

    for tool_fn in [ev.get_event_reel, ev.pull_transcript, ev.write_coach_note, ev.read_coach_notes]:
        doc = (tool_fn.__doc__ or "").lower()
        for banned in BANNED:
            assert banned not in doc, f"Tool {tool_fn.__name__} docstring contains banned instruction '{banned}'"


@pytest.mark.asyncio
async def test_mcp_ui_and_telemetry_endpoints():
    """Verify MCP UI dashboard, status, tool execution, and real-time SSE stream endpoints."""
    app = create_mcp_app()
    test_port = 9062
    config = uvicorn.Config(app, host="127.0.0.1", port=test_port, log_level="error")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()

    for _ in range(20):
        if srv.started:
            break
        await asyncio.sleep(0.05)

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{test_port}", timeout=3.0) as client:
            # 1. UI Root HTML
            r_ui = await client.get("/")
            assert r_ui.status_code == 200
            assert "Cat &amp; Mouse MCP Server" in r_ui.text or "Cat & Mouse" in r_ui.text

            # 2. Server Status
            r_status = await client.get("/api/mcp/status")
            assert r_status.status_code == 200
            data_status = r_status.json()
            assert data_status["server"] == "cat-and-mouse-mcp"
            assert "stats" in data_status

            # 3. Interactive Tool Execution via UI endpoint
            r_tool = await client.post(
                "/api/mcp/execute-tool",
                json={
                    "tool": "write_coach_note",
                    "arguments": {
                        "note": "Integration test note via UI",
                        "category": "observation",
                    },
                },
            )
            assert r_tool.status_code == 200
            res_json = r_tool.json()
            assert res_json["ok"] is True
            assert res_json["tool"] == "write_coach_note"
            assert res_json["result"]["status"] == "saved"

            # 4. History endpoint reflects the execution
            r_hist = await client.get("/api/mcp/history")
            assert r_hist.status_code == 200
            hist_data = r_hist.json()
            assert len(hist_data["events"]) >= 1
            last_event = hist_data["events"][-1]
            assert last_event["type"] == "tool_call"
            assert last_event["tool"] == "write_coach_note"

            # 5. SSE stream delivers initial payload and connects
            async with client.stream("GET", "/api/mcp/stream") as stream:
                assert stream.status_code == 200
                first_lines = []
                async for line in stream.aiter_lines():
                    first_lines.append(line)
                    if "init" in line:
                        break
            # 6. Coach Notes REST API test
            r_notes = await client.get("/api/mcp/coach-notes")
            assert r_notes.status_code == 200
            assert r_notes.json()["ok"] is True
            assert r_notes.json()["count"] >= 1

            # Create note via REST API with linked ticks
            # First seed a mock tick in the transcript
            store.append_tick({
                "tick": 42,
                "observation": {"self": {"position": {"x": 10, "y": 15}, "facing": "east"}, "visible_entities": []},
                "response": {"action": "attack", "thought": "Mouse detected near north corridor"},
                "source": "adk_gemini",
                "tick_type": "play",
                "executed": True,
            })

            r_create = await client.post(
                "/api/mcp/coach-notes",
                json={
                    "note": "REST API note test",
                    "category": "hypothesis",
                    "tags": ["rest", "api"],
                    "linked_ticks": [42],
                }
            )
            assert r_create.status_code == 200
            mem_created = r_create.json()["memory"]
            created_id = mem_created["id"]
            assert mem_created["linked_ticks"] == [42]
            assert len(mem_created["tick_snapshots"]) == 1
            assert mem_created["tick_snapshots"][0]["tick"] == 42
            assert mem_created["tick_snapshots"][0]["action"] == "attack"

            # Update note via REST API
            r_upd = await client.put(
                f"/api/mcp/coach-notes/{created_id}",
                json={"note": "Updated REST API note"}
            )
            assert r_upd.status_code == 200
            assert r_upd.json()["memory"]["note"] == "Updated REST API note"

            # Delete note via REST API
            r_del = await client.delete(f"/api/mcp/coach-notes/{created_id}")
            assert r_del.status_code == 200
            assert r_del.json()["deleted"] is True

            # Standing notes endpoint
            r_standing = await client.post(
                "/api/mcp/coach-standing-notes",
                json={"notes": "- Standing test order 1"}
            )
            # Coach Live Chat REST API & filtering
            r_chat = await client.get("/api/mcp/coach-chat")
            assert r_chat.status_code == 200
            chat_data = r_chat.json()
            assert chat_data["ok"] is True
            assert "messages" in chat_data
            assert "counts" in chat_data
    finally:
        srv.should_exit = True
        t.join(timeout=1.0)


@pytest.mark.asyncio
async def test_coach_chat_filtering_logic():
    """Verify coach chat endpoint filters by agent, sender, and search keyword."""
    app = create_mcp_app()
    from starlette.testclient import TestClient
    tc = TestClient(app)

    # Mock responses from cat agents
    mock_history = [
        {"id": "gen-1-user", "sender": "general", "message": "Why is that order in the dossier?", "source": "user", "tick": 50},
        {"id": "gen-2-agent", "sender": "coach", "message": "Observed 12 mouse captures on east quadrant.", "source": "adk_gemini", "tick": 50},
    ]

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: list(mock_history)
        mock_get.return_value = mock_resp

        # 1. Fetch all
        res = tc.get("/api/mcp/coach-chat")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert len(data["messages"]) >= 2

        # 2. Filter by sender=coach
        res_coach = tc.get("/api/mcp/coach-chat?sender=coach")
        assert res_coach.status_code == 200
        for m in res_coach.json()["messages"]:
            assert m["sender"] == "coach"

        # 3. Filter by search keyword
        res_search = tc.get("/api/mcp/coach-chat?search=quadrant")
        assert res_search.status_code == 200
        messages = res_search.json()["messages"]
        assert len(messages) >= 1
        assert "quadrant" in messages[0]["message"].lower()


@pytest.mark.asyncio
async def test_coach_chat_deduplication():
    """Verify coach chat endpoint automatically deduplicates duplicate messages."""
    app = create_mcp_app()
    from starlette.testclient import TestClient
    tc = TestClient(app)

    # When querying both cat-1 and cat-2, if the responses return identical messages
    duplicate_history = [
        {"id": "gen-1-user", "sender": "general", "message": "Why is that order in the dossier?", "source": "user", "tick": 50},
    ]

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: list(duplicate_history)
        mock_get.return_value = mock_resp

        # Fetch for cat-1 only: returns 1 message
        res_cat1 = tc.get("/api/mcp/coach-chat?cat_id=cat-1")
        assert res_cat1.status_code == 200
        assert len(res_cat1.json()["messages"]) == 1

        # Fetch with a2a store returning same message id
        with patch("actors.a2a.store.get_coach_inbox") as mock_inbox:
            mock_inbox.return_value = [
                {"message_id": "a2a-bcast-1", "sender_id": "coach(cat-1)", "content": "Pact proposal", "timestamp": "12:00:00", "tick": 50}
            ]
            # Fetch for all: target_cats is [cat-1, cat-2]. get_coach_inbox returns a2a-bcast-1 for both
            res_all = tc.get("/api/mcp/coach-chat?cat_id=all")
            assert res_all.status_code == 200
            msgs = res_all.json()["messages"]
            # Verify a2a-bcast-1 appears only ONCE even though queried across multiple cats
            a2a_msgs = [m for m in msgs if m.get("id") == "a2a-bcast-1"]
            assert len(a2a_msgs) == 1


@pytest.mark.asyncio
async def test_notes_page_endpoint():
    """Verify /notes serves the Coach Private Notes HTML page with no-cache headers."""
    app = create_mcp_app()
    from starlette.testclient import TestClient
    tc = TestClient(app)

    res = tc.get("/notes")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Coach Private Notes" in res.text
    assert "TACTICAL NOTEBOOK" in res.text
    assert "no-cache" in res.headers.get("cache-control", "").lower()




