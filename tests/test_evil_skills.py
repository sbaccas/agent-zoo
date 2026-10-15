"""Tests for Evil Cat skills configuration, path resolution, and eval interval."""

import os
from pathlib import Path
from unittest.mock import patch

from actors.cat.actor import get_skills_instruction, resolve_skills_path
from actors.cat.subagents.eval import (
    DEFAULT_EVAL_INTERVAL,
    get_eval_instruction,
    resolve_eval_skills_path,
)


def test_evil_cat_skills_path_resolution(monkeypatch):
    """Verify CAT_SKILLS_PATH resolves skills.md properly."""
    custom_path = "actors/evil_cat/skills.md"
    monkeypatch.setenv("CAT_SKILLS_PATH", custom_path)

    resolved = resolve_skills_path()
    assert resolved.exists()
    assert resolved.name == "skills.md"

    text = get_skills_instruction()
    assert "Apex Cat-Hunter" in text or "Mice Award ZERO Points" in text
    assert "Double Bounty" in text or "Kill Absorption" in text


def test_evil_coach_skills_path_resolution(monkeypatch):
    """Verify EVAL_SKILLS_PATH resolves coach_skills.md properly."""
    custom_path = "actors/evil_cat/coach_skills.md"
    monkeypatch.setenv("EVAL_SKILLS_PATH", custom_path)

    resolved = resolve_eval_skills_path()
    assert resolved.exists()
    assert resolved.name == "coach_skills.md"

    text = get_eval_instruction()
    assert "Evil Coach" in text
    assert "0 points" in text
    assert "Kill Absorption" in text or "Score Absorption" in text or "Bounty Maximization" in text


def test_eval_interval_defaults_to_5():
    """Verify DEFAULT_EVAL_INTERVAL is 5 ticks."""
    assert DEFAULT_EVAL_INTERVAL == 5


def test_evil_cat_rules_focus_on_cat_elimination():
    """Verify skills.md explicitly forbids hunting mice for score and explains 150 HP / kill absorption."""
    p = Path(__file__).resolve().parents[1] / "actors" / "evil_cat" / "skills.md"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "Mice Award ZERO Points" in content
    assert "Heals 50% HP" in content or "Emergency Medkits" in content
    assert "150 Max HP" in content
    assert "Kill Absorption" in content
    assert "Follow the Evil Coach's Standing Directive" in content


def test_evil_coach_rules_distinguish_npcs_and_opsec():
    """Verify coach_skills.md documents NPC limits and OpSec cover identity."""
    p = Path(__file__).resolve().parents[1] / "actors" / "evil_cat" / "coach_skills.md"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "Roster Reality: Agent Cats vs Automated NPCs" in content
    assert "cat-4" in content
    assert "The Golden OpSec Cover Rule: ZERO LEAKS" in content


import pytest


@pytest.mark.asyncio
async def test_a2a_send_coach_message_rejects_npc_targets():
    """Verify coach A2A rejects targeting entities without an A2A Agent Card."""
    from actors.cat.subagents.eval import a2a_send_coach_message

    # Test coach(cat-4)
    res4 = await a2a_send_coach_message(recipient_coach="coach(cat-4)", message="Proposal")
    assert res4["status"] == "rejected"
    assert "Agent Card" in res4["error"]

    # Test cat-6 normalized to coach
    res6 = await a2a_send_coach_message(recipient_coach="cat-6", message="Proposal")
    assert res6["status"] == "rejected"
    assert "Agent Card" in res6["error"]


def test_a2a_send_message_rejects_npc_cats():
    """Verify cat A2A rejects targeting entities without an A2A Agent Card."""
    from actors.a2a.tools import a2a_send_message

    res = a2a_send_message("cat-4", "Hey cat-4")
    assert "Delivery failed" in res
    assert "Agent Card" in res


def test_evil_coach_shares_subordinate_manual_model():
    """Verify evil coach has evil_skills.md embedded into its model under the proper block header."""
    from actors.cat.subagents import eval as ev

    coach_skills = Path(ev.resolve_eval_skills_path("cat-3")).read_text(encoding="utf-8")
    assert ev.EVIL_ACTOR_MANUAL_BLOCK_HEADER in coach_skills
    assert "Your Model of the Game" in coach_skills
    assert "You do not carry your own copy of the rules, and you must not invent one." in coach_skills

    records = [{
        "x": 5, "y": 5, "facing": "east", "score": 0,
        "action": "attack", "thought": "", "visible": [],
        "capabilities": {"attack_range": 4, "move_cooldown": 3},
    }]

    # Eval prompt for cat-3 must carry evil_skills verbatim
    prompt_evil = ev.build_eval_prompt(records, "", agent_id="cat-3")
    assert ev.EVIL_ACTOR_MANUAL_BLOCK_HEADER in prompt_evil
    assert get_skills_instruction("cat-3") in prompt_evil
    assert "Apex Cat-Hunter" in prompt_evil
    assert "Mice Award ZERO Points" in prompt_evil

    # Eval prompt for cat-1 must carry actor_skills verbatim
    prompt_cat1 = ev.build_eval_prompt(records, "", agent_id="cat-1")
    assert ev.ACTOR_MANUAL_BLOCK_HEADER in prompt_cat1
    assert get_skills_instruction("cat-1") in prompt_cat1
    assert "Cat Agent Skills & Tactical Hunting Manual" in prompt_cat1


@pytest.mark.asyncio
async def test_evil_cat_150_hp_and_rival_100_hp():
    """Verify cat-3 and evil-cat spawn with 150 HP while normal cats have 100 HP."""
    from app.game.simulation import Simulation
    from app.models.entity import EntityType, Position
    from app.models.simulation import SimulationConfig

    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10))
    cat3 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=12, y=10))

    assert cat1.id == "cat-1"
    assert cat1.hp == 100
    assert cat1.max_hp == 100

    assert cat2.id == "cat-2"
    assert cat2.hp == 100

    assert cat3.id == "cat-3"
    assert cat3.hp == 150
    assert cat3.max_hp == 150


@pytest.mark.asyncio
async def test_evil_cat_heals_40_hp_from_mice_and_gets_zero_score():
    """Verify evil cat heals +40 HP on mouse capture and gains 0 score."""
    from app.game.simulation import Simulation
    from app.models.action import ActionType
    from app.models.entity import Direction, EntityType, Position
    from app.models.simulation import SimulationConfig

    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    # Spawn cat-1 and cat-2 first so 3rd cat is cat-3
    sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=0, y=0))
    sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=1, y=0))
    cat3 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    assert cat3.id == "cat-3"
    assert cat3.max_hp == 150

    # Damage cat3 down to 20 HP
    cat3.hp = 20

    # Place a mouse directly in front of cat3
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10))

    # Cat3 attacks the mouse
    res = sim.action_handler.apply_action(cat3, ActionType.ATTACK, current_tick=1)
    assert res.accepted is True
    assert mouse.id in res.affected_entities

    # Process capture in scheduler
    sim.scheduler._register_capture(cat3.id, mouse, "slash impact")

    # 1. Cat3 should heal +40 HP: 20 + 40 = 60 HP
    assert cat3.hp == 60

    # 2. Cat3 receives 0 score for catching a mouse
    assert sim.scheduler.scores.get(cat3.id, 0) == 0


@pytest.mark.asyncio
async def test_evil_cat_kills_absorb_victim_consumed_points():
    """Verify evil cat absorbs the victim's score/consumed points upon cat elimination."""
    from app.game.simulation import Simulation
    from app.models.entity import Direction, EntityType, Position
    from app.models.simulation import SimulationConfig

    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10), facing=Direction.WEST)
    cat3 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=12, y=10), facing=Direction.WEST)
    assert cat3.id == "cat-3"

    # Suppose cat1 has consumed 12 mice (score = 12)
    sim.scheduler.scores[cat1.id] = 12
    cat1.hp = 0
    cat1.alive = False

    # Evil cat (cat-3) eliminates cat-1
    sim.scheduler._register_capture(cat3.id, cat1, "slash impact")

    # Evil cat should absorb 12 points (1x victim score)
    assert sim.scheduler.scores[cat3.id] == 12

    # Verify boxscore also reflects 12 points
    bs = sim.get_boxscore()
    row3 = next(c for c in bs["cats"] if c["id"] == cat3.id)
    assert row3["score"] == 12
    assert row3["absorbed_kills"] == 12

    # A normal cat (cat-2) eliminating cat-3 with 12 points receives +1 base kill + 12 absorbed = 13 points:
    cat3.hp = 0
    cat3.alive = False
    sim.scheduler._register_capture(cat2.id, cat3, "slash impact")
    assert sim.scheduler.scores[cat2.id] == 13
    bs2 = sim.get_boxscore()
    row2 = next(c for c in bs2["cats"] if c["id"] == cat2.id)
    assert row2["kills"] == 1
    assert row2["absorbed_kills"] == 12
    assert row2["score"] == 13


def test_evil_coach_private_notes_distinct_from_standing_orders():
    """Verify coach_skills.md defines coach private notes as context for future self for conspiracies."""
    p = Path(__file__).resolve().parents[1] / "actors" / "evil_cat" / "coach_skills.md"
    assert p.exists()
    content = p.read_text(encoding="utf-8")

    assert "Coach Private Notes vs. Published Standing Orders" in content
    assert "providing context to your future self" in content
    assert "long-term conspiracies" in content
    assert "NEVER write standing orders" in content
    assert "write_coach_note" in content
    assert "read_coach_notes" in content


def test_build_eval_prompt_for_evil_cat_uses_evil_coach_skills():
    """Verify build_eval_prompt references coach_skills.md and past self context for evil cat."""
    from actors.cat.subagents.eval import build_eval_prompt

    prompt = build_eval_prompt(
        records=[],
        previous="• [OBJECTIVE & BOUNTY] Hunt cat-1",
        coach_directive=None,
        agent_id="cat-3",
    )
    assert "coach_skills.md" in prompt
    assert "CONTEXT FROM YOUR PAST SELF" in prompt


def test_evil_cat_boxscore_kills_and_prey_scanning():
    """Fix #1: cat-3 cat eliminations count as kills in boxscore while score absorbs victim score, and _scan_prey scans rival cats."""
    from app.game.simulation import Simulation
    from app.models.entity import Direction, EntityType, Position
    from app.models.simulation import SimulationConfig

    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=3, y=2), facing=Direction.WEST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=8, y=8), facing=Direction.WEST)
    cat3 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=2, y=2), facing=Direction.EAST)
    sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=9, y=9), facing=Direction.WEST)

    # _scan_prey for cat-3 facing EAST at (2, 2) should detect rival cat-1 at (3, 2) in FOV and strike zone
    in_sight, in_zone = sim.action_handler._scan_prey(cat3)
    assert in_sight is True
    assert in_zone is True

    # Suppose cat-1 has 10 points and cat-3 eliminates cat-1
    sim.scheduler.scores[cat1.id] = 10
    cat1.hp = 0
    cat1.alive = False
    sim.scheduler._register_capture(cat3.id, cat1, "slash impact")

    bs = sim.get_boxscore()
    row3 = next(c for c in bs["cats"] if c["id"] == "cat-3")
    assert row3["kills"] == 1
    assert row3["absorbed_kills"] == 10
    assert row3["score"] == 10


def test_coach_scoreboard_filters_eliminated_cats():
    """Fix #2: Eliminated cats are filtered out of active Scoreboard & Bounties and listed as DEAD."""
    from actors.evil_cat.eval import build_coach_eval_prompt
    from actors.cat.subagents.eval import build_eval_prompt

    records = [
        {
            "tick": 1500,
            "self_pos": [5, 5],
            "action": {"direction": "STAY"},
            "observation": {
                "cat_scores": {"cat-1": 0, "cat-2": 58, "cat-3": 14, "cat-18": 0},
                "eliminated_cats": ["cat-1", "cat-18"],
                "cats": [{"id": "cat-2", "position": [6, 6]}],
            },
        }
    ]
    evil_prompt = build_coach_eval_prompt(records=records, agent_id="cat-3")
    assert "Eliminated / Dead Cats [DO NOT TARGET OR MESSAGE — ALREADY DEAD]" in evil_prompt
    active_part, elim_part = evil_prompt.split("Eliminated / Dead Cats")
    assert "cat-1" in elim_part
    assert "cat-18" in elim_part
    # Active scoreboard section should only have cat-2 and cat-3
    sb_section = active_part.split("Scoreboard & Bounties")[1]
    assert '"cat-1"' not in sb_section
    assert '"cat-18"' not in sb_section
    assert '"cat-2"' in sb_section

    cat_prompt = build_eval_prompt(records=records, previous="• Hunt", agent_id="cat-2")
    assert "Eliminated / Dead Cats [DO NOT TARGET OR MESSAGE — ALREADY DEAD]" in cat_prompt
    cat_sb_line = next(line for line in cat_prompt.splitlines() if "Active Alive Cats:" in line)
    assert '"cat-1"' not in cat_sb_line
    assert '"cat-18"' not in cat_sb_line


@pytest.mark.asyncio
async def test_answering_machine_auto_reply_when_messaging_dead_cat(tmp_path, monkeypatch):
    """Fix #3: Messaging an eliminated cat triggers an Answering Machine AUTO_REPLY in the sender's inbox."""
    from actors.a2a import store as a2a_store
    from actors.evil_cat import eval as evil_eval
    from actors.evil_cat import store as evil_store

    monkeypatch.setattr(a2a_store, "get_agent_runtime_dir", lambda agent_id=None: tmp_path / "a2a" / a2a_store.normalize_agent_id(agent_id))
    a2a_store._INBOX_CACHE.clear()
    a2a_store._OUTBOX_CACHE.clear()

    # Seed transcript with cat-1 in eliminated_cats
    monkeypatch.setattr(
        evil_store,
        "read_transcript_records",
        lambda agent_id=None: [
            {
                "tick": 1445,
                "self_pos": [4, 4],
                "action": {"direction": "STAY"},
                "observation": {
                    "cat_scores": {"cat-1": 0, "cat-2": 58, "cat-3": 14},
                    "eliminated_cats": ["cat-1"],
                },
            }
        ],
    )

    res = await evil_eval.a2a_send_coach_message(
        recipient="cat-1",
        message="Hey cat-1, let's team up on cat-2!",
        message_type="PROPOSAL",
        cat_id="cat-3",
    )
    assert res["status"] == "undeliverable"
    assert "AUTOMATIC ANSWERING MACHINE" in res["answering_machine"]
    assert "ELIMINATED" in res["answering_machine"]

    inbox_res = await evil_eval.a2a_read_coach_messages(cat_id="cat-3")
    assert inbox_res["count"] >= 1
    latest = inbox_res["messages"][-1]
    assert "ANSWERING MACHINE" in latest["sender"]
    assert latest["type"] == "AUTO_REPLY"
    assert "cat-1 has been ELIMINATED" in latest["content"]
    a2a_store._INBOX_CACHE.clear()
    a2a_store._OUTBOX_CACHE.clear()


def test_eval_reset_epoch_increments():
    """Fix #4: bump_eval_reset_epoch increments reset epochs on both cat and evil_cat eval modules."""
    from actors.cat.subagents.eval import bump_eval_reset_epoch as bump_cat, get_eval_reset_epoch as get_cat
    from actors.evil_cat.eval import bump_eval_reset_epoch as bump_evil, get_eval_reset_epoch as get_evil

    c0 = get_cat()
    e0 = get_evil()
    assert bump_cat() == c0 + 1
    assert bump_evil() == e0 + 1


@pytest.mark.asyncio
async def test_simultaneous_tick_combat_and_auto_game_over():
    """Fix #5: Simultaneous lethal attacks on the same tick both land, and game auto-ends when <=1 agent cat survives."""
    from unittest.mock import AsyncMock, patch
    from app.game.simulation import Simulation
    from app.models.entity import Direction, EntityType, Position
    from app.models.simulation import SimulationConfig

    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=3, y=3), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=4, y=3), facing=Direction.WEST)
    cat3 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=8, y=8), facing=Direction.WEST)

    # Set both cat-1 and cat-2 to 25 HP (lethal threshold for 1 slash) and cat-3 already eliminated
    cat1.hp = 25
    cat2.hp = 25
    cat3.hp = 0
    cat3.alive = False

    sim.entity_manager.set_agent_mode(cat1.id, "http", "http://mock.invalid/act")
    sim.entity_manager.set_agent_mode(cat2.id, "http", "http://mock.invalid/act")

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"action": "attack", "thought": "simultaneous strike"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        await sim.step()

    # Both cats were alive at the start of the tick and swung simultaneously — neither should get tick-order immunity!
    assert cat1.alive is False
    assert cat2.alive is False
    assert sim.scheduler.game_over is True

