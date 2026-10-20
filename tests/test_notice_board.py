"""Tests for the Global Notice Board, Kill Absorption rule, and MCP sync."""

import asyncio
from pathlib import Path
import pytest

from actors.cat import store
from actors.cat.actor import build_decision_prompt
from actors.models import Observation
from app.game.simulation import Simulation
from app.models.action import ActionType
from app.models.entity import Direction, EntityType, Position
from app.models.simulation import SimulationConfig, SpeciesConfig
from app.mcp.server import create_mcp_server, _build_notice_board_data


@pytest.fixture(autouse=True)
def clean_runtime_store(tmp_path, monkeypatch):
    """Ensure tests run against an isolated runtime directory."""
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(store, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(store, "TRANSCRIPT_PATH", tmp_path / "transcript.jsonl")
    monkeypatch.setattr(store, "COACH_MEMORY_PATH", tmp_path / "coach_memory.jsonl")
    monkeypatch.setattr(store, "COACHING_NOTES_PATH", tmp_path / "coaching_notes.md")
    monkeypatch.setattr(store, "NOTICE_BOARD_PATH", tmp_path / "notice_board.md")
    store.reset_notice_board()
    store.reset_coaching_notes()
    yield
    store.reset_notice_board()
    store.reset_coaching_notes()


def test_species_config_absorption_field():
    """Verify absorption field on SpeciesConfig defaults to True."""
    cfg = SpeciesConfig()
    assert hasattr(cfg, "absorption")
    assert cfg.absorption is True

    cfg_disabled = SpeciesConfig(absorption=False)
    assert cfg_disabled.absorption is False


@pytest.mark.asyncio
async def test_kill_absorption_transfers_score_on_elimination():
    """When absorption is active, eliminating a rival cat absorbs their accumulated score."""
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    sim.entity_manager.species_config["cat"].absorption = True

    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10), facing=Direction.WEST)

    # Give cat2 an accumulated score of 5 kills, cat1 has 2
    sim.scheduler.scores[cat2.id] = 5
    sim.scheduler.scores[cat1.id] = 2

    # Knockout cat2 and register capture
    cat2.hp = 0
    cat2.alive = False
    sim.scheduler._register_capture(cat1.id, cat2, "slash impact")

    # cat1 gets 2 (prior) + 1 (knockout) + 5 (absorbed) = 8!
    assert sim.scheduler.scores[cat1.id] == 8


@pytest.mark.asyncio
async def test_kill_without_absorption_awards_single_point():
    """When absorption is disabled, eliminating a rival cat awards only 1 point."""
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    sim.entity_manager.species_config["cat"].absorption = False

    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10), facing=Direction.WEST)

    sim.scheduler.scores[cat2.id] = 5
    sim.scheduler.scores[cat1.id] = 2

    cat2.hp = 0
    cat2.alive = False
    sim.scheduler._register_capture(cat1.id, cat2, "slash impact")

    # Standard rule: 2 + 1 = 3 (no absorption)
    assert sim.scheduler.scores[cat1.id] == 3


def test_notice_board_store_persistence():
    """Verify notice board read/write and persistence in runtime store."""
    bulletin = "# 📢 ARENA NOTICE\nRule Modifier: Kill Absorption is ACTIVE"
    store.write_notice_board(bulletin)
    assert store.read_notice_board() == bulletin

    store.reset_notice_board()
    assert store.read_notice_board() == ""


def test_build_decision_prompt_injects_notice_board():
    """Verify build_decision_prompt includes Notice Board and absorption capabilities."""
    store.write_notice_board("📢 ACTIVE MODIFIER: KILL ABSORPTION ENABLED")

    obs = Observation(
        tick=10,
        round=1,
        self={"id": "cat-1", "hp": 100, "max_hp": 100, "position": {"x": 5, "y": 5}, "facing": "north"},
        capabilities={"absorption": True, "attack_power": 34, "attack_range": 4},
        score=0,
        scoreboard={"cat-1": 0, "cat-2": 4},
    )

    capture = {}
    prompt = build_decision_prompt(obs, capture=capture)

    assert "--- GLOBAL ARENA NOTICE BOARD ---" in prompt
    assert "KILL ABSORPTION ENABLED" in prompt
    assert "kill_absorption: ACTIVE" in prompt
    assert "notice_board" in capture["sections"]
    assert capture["sections"]["notice_board"]["delivered"] is True


@pytest.mark.asyncio
async def test_mcp_get_notice_board_tool(monkeypatch):
    """Test get_notice_board tool registers and returns formatted notice data."""
    server = create_mcp_server()

    # Mock _fetch_from_game_server to return species config with absorption=True
    async def mock_fetch(endpoint, params=None, method="GET", json_body=None):
        if "species" in endpoint:
            return True, {"cat": {"absorption": True, "aim_assist": True}}, None
        if "state" in endpoint:
            return True, {"tick": 128, "round": 1}, None
        return False, None, "not found"

    monkeypatch.setattr("app.mcp.server._fetch_from_game_server", mock_fetch)

    res = await server.call_tool("get_notice_board", {"sim_id": "active"})
    result = res.structured_content["result"]

    assert result["available"] is True
    assert result["tick"] == 128
    assert result["active_modifiers"]["absorption"]["enabled"] is True
    assert result["active_modifiers"]["absorption"]["status"] == "ACTIVE"
    assert "KILL ABSORPTION" in result["bulletin"]
    assert "PREDATION HP RESTORATION" in result["bulletin"]
    assert "+40 HP" in result["bulletin"]
    assert "+100 HP" in result["bulletin"]
    assert result["active_modifiers"]["predation_healing"]["enabled"] is True
    assert result["active_modifiers"]["predation_healing"]["mouse_heal"] == 40
    assert result["active_modifiers"]["predation_healing"]["cat_heal"] == 100
    # Check that it automatically synced to store
    assert store.read_notice_board() == result["bulletin"]


@pytest.mark.asyncio
async def test_mouse_capture_heals_cat_hp():
    """Capturing a mouse restores +40 HP up to max_hp."""
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10))

    cat.hp = 50
    mouse.alive = False
    sim.scheduler._register_capture(cat.id, mouse, "slash impact")

    # 50 + 40 = 90
    assert cat.hp == 90

    # Capped at max_hp
    cat.hp = 85
    mouse2 = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=12, y=10))
    mouse2.alive = False
    sim.scheduler._register_capture(cat.id, mouse2, "slash impact")
    assert cat.hp == 100


@pytest.mark.asyncio
async def test_cat_elimination_heals_cat_hp():
    """Eliminating a rival cat restores full HP (max_hp)."""
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10))

    cat1.hp = 5
    cat2.alive = False
    sim.scheduler._register_capture(cat1.id, cat2, "slash impact")

    # Restored to full max_hp (100) for standard cat
    assert cat1.hp == 100

    # Evil cat (cat-3, 150 max_hp) also heals back to full max_hp (150)
    cat3 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=12, y=10))
    cat4 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=13, y=10))
    assert cat3.id == "cat-3" and cat3.max_hp == 150
    cat3.hp = 25
    cat4.alive = False
    sim.scheduler._register_capture(cat3.id, cat4, "slash impact")
    assert cat3.hp == 150

