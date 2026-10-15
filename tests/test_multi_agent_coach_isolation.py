"""Tests for multi-agent coach, store, and radio isolation between cat-1 and cat-2.

Verifies:
1. File paths on disk are strictly separated per agent (runtime_cat-1 vs runtime_cat-2).
2. Coaching notes written for cat-1 never leak into cat-2.
3. Coach memories written for cat-1 never leak into cat-2.
4. Evaluation prompt includes dedicated coach assignment.
5. General Radio replies address the specific cat assigned to the coach.
6. MCP server tools route correctly by cat_id.
"""

import asyncio
from pathlib import Path
import tempfile
import pytest

from actors.cat import store
from actors.cat.subagents import eval as ev
from app.mcp.server import create_mcp_server


@pytest.fixture
def isolated_dirs(monkeypatch):
    """Creates isolated temporary directories for cat-1 and cat-2."""
    tmp_base = Path(tempfile.mkdtemp())
    dir_cat1 = tmp_base / "runtime_cat-1"
    dir_cat2 = tmp_base / "runtime_cat-2"
    dir_cat1.mkdir(parents=True, exist_ok=True)
    dir_cat2.mkdir(parents=True, exist_ok=True)

    # Ensure CAT_AGENT_RUNTIME_DIR is not set so store routes dynamically by cat id
    monkeypatch.delenv("CAT_AGENT_RUNTIME_DIR", raising=False)

    def custom_get_cat_paths(agent_id: str):
        target = dir_cat2 if agent_id == "cat-2" else dir_cat1
        target.mkdir(parents=True, exist_ok=True)
        return {
            "runtime_dir": target,
            "transcript": target / "transcript.jsonl",
            "coaching_notes": target / "coaching_notes.md",
            "notice_board": target / "notice_board.md",
            "coach_memory": target / "coach_memory.jsonl",
            "prompts": target / "prompts.jsonl",
            "run_config": target / "run_config.json",
        }

    monkeypatch.setattr(store, "get_cat_paths", custom_get_cat_paths)
    yield dir_cat1, dir_cat2


def test_coaching_notes_isolated_between_cats(isolated_dirs):
    dir_cat1, dir_cat2 = isolated_dirs

    # Write coaching notes specifically for cat-1
    store.write_coaching_notes("- Hold the northern choke point", agent_id="cat-1")
    # Write different coaching notes specifically for cat-2
    store.write_coaching_notes("- Flank from the southern corridor", agent_id="cat-2")

    notes_cat1 = store.read_coaching_notes(agent_id="cat-1")
    notes_cat2 = store.read_coaching_notes(agent_id="cat-2")

    assert "Hold the northern choke point" in notes_cat1
    assert "Flank from the southern corridor" not in notes_cat1

    assert "Flank from the southern corridor" in notes_cat2
    assert "Hold the northern choke point" not in notes_cat2


def test_coach_memories_isolated_between_cats(isolated_dirs):
    dir_cat1, dir_cat2 = isolated_dirs

    store.append_coach_memory("Cat 1 mouse nest detected at (2,3)", category="intel", agent_id="cat-1")
    store.append_coach_memory("Cat 2 detected mouse clustering at (18,19)", category="intel", agent_id="cat-2")

    mems_cat1 = store.read_coach_memories(agent_id="cat-1")
    mems_cat2 = store.read_coach_memories(agent_id="cat-2")

    assert len(mems_cat1) == 1
    assert "Cat 1 mouse nest" in mems_cat1[0]["note"]

    assert len(mems_cat2) == 1
    assert "Cat 2 detected mouse clustering" in mems_cat2[0]["note"]


def test_eval_prompt_includes_dedicated_assignment(isolated_dirs):
    prompt_cat1 = ev.build_eval_prompt([], "", agent_id="cat-1")
    prompt_cat2 = ev.build_eval_prompt([], "", agent_id="cat-2")

    assert "DEDICATED COACH FOR CAT-1" in prompt_cat1
    assert "DEDICATED COACH FOR CAT-2" in prompt_cat2


@pytest.mark.asyncio
async def test_general_radio_reply_reflects_specific_cat(isolated_dirs):
    reply_cat1, engine_cat1, _ = await ev.interrogate_general(
        "Why are you holding?",
        notes="- Hold perimeter",
        agent_id="cat-1",
    )
    reply_cat2, engine_cat2, _ = await ev.interrogate_general(
        "Why are you holding?",
        notes="- Scout center",
        agent_id="cat-2",
    )

    # In offline fallback (pytest mode), reply specifically mentions the targeted cat
    assert "CAT-1" in reply_cat1
    assert "CAT-2" in reply_cat2


@pytest.mark.asyncio
async def test_mcp_server_routes_coach_notes_by_cat_id(isolated_dirs):
    server = create_mcp_server()

    # Call write_coach_note for cat-1 and cat-2
    await server.call_tool("write_coach_note", {"note": "Cat-1 specific note", "cat_id": "cat-1"})
    await server.call_tool("write_coach_note", {"note": "Cat-2 specific note", "cat_id": "cat-2"})

    res_cat1 = await server.call_tool("read_coach_notes", {"cat_id": "cat-1"})
    res_cat2 = await server.call_tool("read_coach_notes", {"cat_id": "cat-2"})

    mems1 = res_cat1.structured_content["result"].get("memories", [])
    mems2 = res_cat2.structured_content["result"].get("memories", [])

    notes1 = [m["note"] for m in mems1]
    notes2 = [m["note"] for m in mems2]

    assert "Cat-1 specific note" in notes1
    assert "Cat-2 specific note" not in notes1

    assert "Cat-2 specific note" in notes2
    assert "Cat-1 specific note" not in notes2

    # Reading with cat_id="all" returns notes from both cats
    res_all = await server.call_tool("read_coach_notes", {"cat_id": "all"})
    all_notes = [m["note"] for m in res_all.structured_content["result"].get("memories", [])]
    assert "Cat-1 specific note" in all_notes
    assert "Cat-2 specific note" in all_notes

    # MCP resources for cat-1 and cat-2
    res_r_cat1 = await server.read_resource("cm://coach/cat-1/notes")
    res_r_cat2 = await server.read_resource("cm://coach/cat-2/notes")
    res_r_all = await server.read_resource("cm://coach/notes")

    import json
    p1 = json.loads(res_r_cat1[0].content)
    p2 = json.loads(res_r_cat2[0].content)
    pall = json.loads(res_r_all[0].content)

    assert p1["count"] == 1
    assert p1["memories"][0]["note"] == "Cat-1 specific note"
    assert p2["count"] == 1
    assert p2["memories"][0]["note"] == "Cat-2 specific note"
    assert pall["count"] == 2


@pytest.mark.asyncio
async def test_mcp_rest_endpoint_coach_notes_multi_agent(isolated_dirs):
    from starlette.testclient import TestClient
    from app.mcp.server import create_mcp_app

    store.append_coach_memory("Cat 1 stealth plan", category="tactical", agent_id="cat-1")
    store.append_coach_memory("Cat 2 pincer plan", category="tactical", agent_id="cat-2")

    app = create_mcp_app()
    with TestClient(app) as client:
        # Default / all cats
        resp_all = client.get("/api/mcp/coach-notes")
        assert resp_all.status_code == 200
        data_all = resp_all.json()
        assert data_all["ok"] is True
        assert data_all["counts"]["all"] == 2
        assert data_all["counts"]["cat-1"] == 1
        assert data_all["counts"]["cat-2"] == 1
        assert len(data_all["memories"]) == 2

        # Cat 1 only
        resp_c1 = client.get("/api/mcp/coach-notes?cat_id=cat-1")
        assert resp_c1.status_code == 200
        data_c1 = resp_c1.json()
        assert len(data_c1["memories"]) == 1
        assert data_c1["memories"][0]["note"] == "Cat 1 stealth plan"

        # Cat 2 only
        resp_c2 = client.get("/api/mcp/coach-notes?cat_id=cat-2")
        assert resp_c2.status_code == 200
        data_c2 = resp_c2.json()
        assert len(data_c2["memories"]) == 1
        assert data_c2["memories"][0]["note"] == "Cat 2 pincer plan"


@pytest.mark.asyncio
async def test_coach_private_notes_stamped_with_tick_id(isolated_dirs, monkeypatch):
    import json
    from actors.evil_cat import store as evil_store
    from actors.evil_cat import eval as evil_ev

    dir_cat1, dir_cat2 = isolated_dirs
    dir_cat3 = dir_cat1.parent / "runtime_cat-3"
    dir_cat3.mkdir(parents=True, exist_ok=True)

    orig_get_cat_paths = store.get_cat_paths

    def get_all_paths(agent_id: str):
        if agent_id in ("cat-3", "evil-cat"):
            return {
                "runtime_dir": dir_cat3,
                "transcript": dir_cat3 / "transcript.jsonl",
                "coaching_notes": dir_cat3 / "coaching_notes.md",
                "notice_board": dir_cat3 / "notice_board.md",
                "coach_memory": dir_cat3 / "coach_memory.jsonl",
                "prompts": dir_cat3 / "prompts.jsonl",
                "coach_prompts": dir_cat3 / "coach_prompts.jsonl",
                "run_config": dir_cat3 / "run_config.json",
            }
        return orig_get_cat_paths(agent_id)

    monkeypatch.setattr(store, "get_cat_paths", get_all_paths)
    monkeypatch.setattr(evil_store, "get_cat_paths", get_all_paths)
    monkeypatch.setenv("CAT_MCP_SERVER_URL", "")
    monkeypatch.setenv("MCP_SERVER_URL", "")

    # Seed transcript ticks for cat-1 (tick 420) and cat-3 (tick 1185)
    (dir_cat1 / "transcript.jsonl").write_text(
        json.dumps({"tick": 420, "x": 5, "y": 6, "facing": "north", "action": "move", "score": 120}) + "\n",
        encoding="utf-8",
    )
    (dir_cat3 / "transcript.jsonl").write_text(
        json.dumps({"tick": 1185, "x": 14, "y": 14, "facing": "east", "action": "slash", "score": 335}) + "\n",
        encoding="utf-8",
    )

    # 1. Calling write_coach_note without explicit tick stamps from latest transcript tick
    res1 = await ev.write_coach_note("Cluster spotted near (5,6)", category="intel", cat_id="cat-1")
    assert res1["memory"]["tick"] == 420

    res3 = await evil_ev.write_coach_note("Cat-1 eliminated; hunting mice", category="deception", cat_id="cat-3")
    assert res3["memory"]["tick"] == 1185

    # 2. Calling MCP write_coach_note without explicit tick stamps from transcript or linked_ticks
    server = create_mcp_server()
    mcp_res = await server.call_tool("write_coach_note", {"note": "MCP note for cat-1", "cat_id": "cat-1"})
    mcp_mem = mcp_res.structured_content["result"]["memory"]
    assert mcp_mem["tick"] == 420

    mcp_res_linked = await server.call_tool(
        "write_coach_note",
        {"note": "MCP note for cat-2 with linked tick", "cat_id": "cat-2", "linked_ticks": [777]},
    )
    mcp_mem_linked = mcp_res_linked.structured_content["result"]["memory"]
    assert mcp_mem_linked["tick"] == 777

    # 3. Verify both Coach eval prompts display Tick #<tick> in the private notebook section
    prompt_cat1 = ev.build_eval_prompt([], "", agent_id="cat-1")
    assert "[Tick #420 | intel] Cluster spotted near (5,6)" in prompt_cat1

    prompt_cat3 = evil_ev.build_coach_eval_prompt([], agent_id="cat-3")
    assert "[Tick #1185 | deception] Cat-1 eliminated; hunting mice" in prompt_cat3


