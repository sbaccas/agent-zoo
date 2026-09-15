"""The prompt builder emits data; the markdown manuals emit instructions.

This file exists because the two were mixed, and the mixture silently beat the
manual. `actor_skills.md` is the system instruction, so it sits furthest from
the decision. Meanwhile `build_decision_prompt` appended its own imperatives at
the very end of the user turn -- including a `TACTICAL RULE:` line that told the
model to prioritise `attack`, contradicting the manual's own matrix.

Measured consequence, over 540 live Gemini decisions: `lunge` was legal in 524
of them, a mouse was visible in 109 of them, and `lunge` was chosen in exactly
zero. Editing the manual could not fix that, because the manual was only about a
third of what the model was actually reading.

The rule these tests enforce:

    Python emits facts and delimiters. Markdown emits every imperative,
    priority, and interpretation.

Instruction text is extracted with `ast` rather than by grepping the file, so
comments and docstrings -- which legitimately discuss imperatives, as this one
does -- cannot trigger or mask a failure.
"""

import ast
from pathlib import Path

import pytest

from actors.cat.actor import (
    COACH_BLOCK_HEADER,
    COMMANDER_BLOCK_HEADER,
    build_decision_prompt,
)
from actors.models import Observation

ACTOR_PY = Path(__file__).resolve().parents[1] / "actors" / "cat" / "actor.py"
SKILLS_MD = Path(__file__).resolve().parents[1] / "actors" / "cat" / "actor_skills.md"
RADIO_MD = Path(__file__).resolve().parents[1] / "actors" / "cat" / "radio_skills.md"

# Second-person instruction markers. A string the model reads containing one of
# these is telling it how to behave, which is the manual's job.
BANNED = (
    "outranks",
    "comply",
    "prioritize",
    "prioritise",
    "you must",
    "you should",
    "make sure",
    "remember to",
    "tactical rule",
    "immediately!",
    "highest authority",
    "honour them",
    "honor them",
)

# Functions whose string literals reach the model.
PROMPT_BUILDERS = ("build_decision_prompt", "interrogate_adk_gemini")


def _model_facing_literals(func_name: str) -> list[str]:
    """Every string constant inside `func_name`, minus its docstring."""
    tree = ast.parse(ACTOR_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]  # drop the docstring
            return [
                n.value
                for stmt in body
                for n in ast.walk(stmt)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
    raise AssertionError(f"{func_name} not found in {ACTOR_PY}")


def _observation(tick: int = 30) -> Observation:
    return Observation(
        tick=tick,
        round=1,
        agent_id="cat-1",
        self={
            "id": "cat-1",
            "position": {"x": 19, "y": 51},
            "facing": "east",
            "type": "cat",
        },
        visible_entities=[],
        legal_actions=["stay", "north", "south", "east", "west", "attack", "lunge"],
        capabilities={
            "attack_range": 8,
            "vision_radius": 10,
            "move_cooldown": 3,
            "attack_cooldown": 3,
            "lunge_cooldown": 2,
        },
        score=4,
        scoreboard={"cat-1": 4},
    )


# --------------------------------------------------------------------------
# The rule itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("func_name", PROMPT_BUILDERS)
@pytest.mark.parametrize("banned", BANNED)
def test_prompt_builders_contain_no_instructions(func_name, banned):
    """No prompt-building literal may instruct the model."""
    for literal in _model_facing_literals(func_name):
        assert banned not in literal.lower(), (
            f"{func_name} contains the instruction {banned!r} in {literal[:80]!r}. "
            f"Move it to actor_skills.md -- Python emits data only."
        )


def test_built_prompt_contains_no_instructions():
    """The rule, checked on the assembled output rather than the source.

    Coach notes and the commander's own words are excluded: those are authored
    elsewhere and will legitimately contain imperatives. What is under test is
    the scaffolding Python wraps around them.
    """
    prompt = build_decision_prompt(_observation(), history=[])
    lowered = prompt.lower()
    for banned in BANNED:
        assert banned not in lowered, f"the prompt scaffolding instructs: {banned!r}"


def test_the_specific_line_that_suppressed_lunge_is_gone():
    """Regression guard, named after the bug.

    `TACTICAL RULE: ... prioritize 'attack' ...` was the final line of the
    observation block. Over 109 live decisions with a mouse in view it produced
    61 attacks and 0 lunges.
    """
    prompt = build_decision_prompt(_observation(), history=[])
    assert "TACTICAL RULE" not in prompt
    assert "prioritize" not in prompt.lower()


def test_prompt_still_carries_the_facts_it_is_supposed_to():
    """Deleting instructions must not have deleted the data with them."""
    prompt = build_decision_prompt(_observation(), history=[])
    for fact in (
        "attack_range: 8",
        "vision_radius: 10",
        "move_cooldown: 3",
        "attack_cooldown: 3",
        "lunge_cooldown: 2",
        "Current Facing: east",
        "Your Score: 4",
        "Legal Actions this tick:",
    ):
        assert fact in prompt, f"the observation stopped reporting {fact!r}"


# --------------------------------------------------------------------------
# Headers are labels shared between code and manual
# --------------------------------------------------------------------------

def test_block_headers_are_bare_labels():
    """A header is a name, not a sentence. Prose here is instruction smuggling."""
    for header in (COACH_BLOCK_HEADER, COMMANDER_BLOCK_HEADER):
        assert header.startswith("--- ") and header.endswith(" ---")
        words = header.strip("- ").split()
        assert len(words) <= 4, f"{header!r} is a sentence, not a label"


# --------------------------------------------------------------------------
# What the manual must now carry, since Python no longer does
# --------------------------------------------------------------------------

def test_manual_defines_what_a_tick_is():
    """The actor priced every action wrongly because nothing defined a tick."""
    manual = SKILLS_MD.read_text().lower()
    assert "a **tick** is" in manual, "the manual never defines the unit of time"
    assert "lock" in manual, "the manual never explains that actions lock you"


def test_manual_states_the_per_action_tick_cost():
    """Without the costs, `lunge` cannot be evaluated against walking."""
    manual = SKILLS_MD.read_text()
    assert "Tiles per tick" in manual, "no cost table"
    # The three numbers that make lunge worth choosing.
    assert "0.33" in manual, "walking speed is not quantified"
    assert "1.00" in manual, "lunge speed is not quantified"
    assert "three times faster" in manual, (
        "the manual states both speeds but never draws the comparison"
    )


def test_manual_says_lunge_cannot_kill():
    """Engine truth: only ATTACK populates hit_entities. See app/game/actions.py."""
    manual = SKILLS_MD.read_text().lower()
    assert "lunge` cannot kill" in manual or "a lunge cannot kill" in manual, (
        "the manual never states that lunging into a mouse does nothing"
    )


def test_manual_says_only_attack_scores():
    manual = SKILLS_MD.read_text().lower()
    assert "only action that kills" in manual


def test_manual_says_a_lock_prevents_attacking():
    """The non-obvious consequence: prey can arrive while you cannot answer."""
    manual = SKILLS_MD.read_text().lower()
    assert "including\n`attack`" in manual or "including `attack`" in manual, (
        "the manual never says the recovery lock blocks attacking too"
    )


# --------------------------------------------------------------------------
# The radio persona left Python
# --------------------------------------------------------------------------

def test_radio_persona_lives_in_markdown():
    assert RADIO_MD.exists(), "radio_skills.md is missing"
    assert len(RADIO_MD.read_text().strip()) > 200


def test_interrogator_instruction_is_assembled_from_both_manuals():
    from actors.cat.actor import _interrogator_instruction_provider

    combined = _interrogator_instruction_provider(None)
    assert SKILLS_MD.read_text().strip() in combined
    assert RADIO_MD.read_text().strip() in combined


def test_missing_skills_manual_fails_loudly():
    """The old fallback string contradicted the manual and applied silently.

    It told the cat to "close distance with lunges" while the manual ranked
    lunge second behind attack. A missing manual must stop the agent, not
    quietly swap in a different policy.
    """
    import actors.cat.actor as actor_mod

    original = actor_mod.SKILLS_PATH
    actor_mod.SKILLS_PATH = Path("/nonexistent/actor_skills.md")
    try:
        with pytest.raises(FileNotFoundError):
            actor_mod.get_skills_instruction()
    finally:
        actor_mod.SKILLS_PATH = original
