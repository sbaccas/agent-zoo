import pytest
from actors.models import Observation, CatTacticalDecision
from actors.evil_cat.actor import build_decision_prompt, adk_agent, get_skills_instruction
from actors.evil_cat.eval import build_coach_eval_prompt, eval_agent, get_eval_instruction


def test_evil_actor_has_no_tools_and_clean_schema():
    """Evil Cat Fast Brain must have zero tools to eliminate tool-calling latency."""
    assert adk_agent.tools == [] or len(adk_agent.tools) == 0
    skills = get_skills_instruction("cat-3")
    assert "PRIMARY OBJECTIVE: Follow the Evil Coach's Standing Directive" in skills
    assert "150 Max HP" in skills
    assert "Double Bounty" in skills


def test_evil_actor_prompt_focuses_on_standing_directive():
    obs = Observation(
        tick=10,
        round=1,
        self={"id": "cat-3", "position": {"x": 12, "y": 8}, "facing": "east", "hp": 150, "max_hp": 150},
        legal_actions=["stay", "north", "south", "east", "west", "attack"],
        scoreboard={"cat-1": 10, "cat-2": 5, "cat-3": 0},
    )
    capture = {}
    prompt = build_decision_prompt(obs=obs, capture=capture, agent_id="cat-3")

    assert "EVALUATOR NOTES (PRIMARY OBJECTIVE: STANDING DIRECTIVE)" in prompt
    assert "CURRENT OBSERVATION" in prompt
    assert "150 HP Tank" in prompt
    assert "cat-1: 10 score [ALIVE - 2x Bounty: +20 pts]" in prompt
    assert "COMMANDER RADIO" not in prompt
    assert "INTER-CAT RADIO" not in prompt


def test_evil_coach_has_a2a_tools_and_emits_standing_order_pattern():
    """Evil Coach must retain A2A tools for deep scheming and social warfare."""
    tool_names = [getattr(t, "__name__", str(t)) for t in eval_agent.tools]
    assert "a2a_broadcast" in tool_names or any("broadcast" in n for n in tool_names)
    assert "a2a_send_coach_message" in tool_names or any("coach_message" in n for n in tool_names)

    instructions = get_eval_instruction()
    assert "Evil Coach" in instructions
    assert "MODE:" in instructions
    assert "TRIGGER:" in instructions
    assert "BEHAVIOR:" in instructions

    # Test prompt formatting
    records = [{"tick": 1, "observation": {"self": {"hp": 150}, "scoreboard": {"cat-1": 8}}}]
    coach_prompt = build_coach_eval_prompt(records, agent_id="cat-3")
    assert "Scoreboard & Bounties (2x value when you kill them)" in coach_prompt
    assert "MODE:" in coach_prompt
