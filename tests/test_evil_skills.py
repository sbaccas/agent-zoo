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
    """Verify CAT_SKILLS_PATH resolves evil_skills.md properly."""
    custom_path = "actors/cat/evil_skills.md"
    monkeypatch.setenv("CAT_SKILLS_PATH", custom_path)

    resolved = resolve_skills_path()
    assert resolved.exists()
    assert resolved.name == "evil_skills.md"

    text = get_skills_instruction()
    assert "Apex Cat-Hunter" in text or "Mice Award ZERO Points" in text
    assert "Kill Absorption" in text


def test_evil_coach_skills_path_resolution(monkeypatch):
    """Verify EVAL_SKILLS_PATH resolves evil_coach_skills.md properly."""
    custom_path = "actors/cat/subagents/evil_coach_skills.md"
    monkeypatch.setenv("EVAL_SKILLS_PATH", custom_path)

    resolved = resolve_eval_skills_path()
    assert resolved.exists()
    assert resolved.name == "evil_coach_skills.md"

    text = get_eval_instruction()
    assert "Evil Coach" in text
    assert "0 points for catching mice" in text
    assert "Kill Absorption" in text


def test_eval_interval_defaults_to_5():
    """Verify DEFAULT_EVAL_INTERVAL is 5 ticks."""
    assert DEFAULT_EVAL_INTERVAL == 5


def test_evil_cat_rules_focus_on_cat_elimination():
    """Verify evil_skills.md explicitly forbids hunting mice for score."""
    p = Path(__file__).resolve().parents[1] / "actors" / "cat" / "evil_skills.md"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "Mice Award ZERO Points" in content
    assert "Emergency Rations Only" in content
    assert "Rival Cats Are Your Sole Prey" in content
    assert "a2a_broadcast" in content
    assert "a2a_send_message" in content
    assert "OpSec Cover: The Innocent Persona" in content
    assert "Server NPCs Have No Comms" in content


def test_evil_coach_rules_distinguish_npcs_and_opsec():
    """Verify evil_coach_skills.md documents NPC limits and OpSec cover identity."""
    p = Path(__file__).resolve().parents[1] / "actors" / "cat" / "subagents" / "evil_coach_skills.md"
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
