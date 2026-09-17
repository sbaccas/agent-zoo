"""The coach and the actor must share exactly one model of the game.

The evaluator used to carry its own prose summary of the rules in
eval_skills.md. Nothing kept that summary honest, and it rotted:

  * it called the slash an "expanding 60 degree cone" long after the engine,
    the heuristic and actor_skills.md had all settled on a 3-wide rectangle;
  * its worked examples told the coach to align the cat to a mouse's "exact
    row or column", when offaxis of -1 and +1 both connect.

Neither error was a hallucination. The coach was copying the model answers in
its own manual, and the cat then followed the resulting directive verbatim.
Measured over one run: 38 swings, 3 connected, and 52% of the game spent
locked in cooldown paying for the misses.

The fix is structural rather than editorial. The coach is handed the actor's
manual and the actor's live capabilities, and its own manual is forbidden from
restating either. These tests enforce that arrangement, because fixing the
prose alone would leave nothing stopping it from drifting again.
"""

import re
from pathlib import Path

import pytest

from actors.cat.actor import SKILLS_PATH, get_skills_instruction
from actors.cat.subagents import eval as ev

EVAL_SKILLS = Path(ev.EVAL_SKILLS_PATH)


def _records(n=3, attack_range=4, **caps):
    base = {
        "attack_range": attack_range,
        "vision_radius": 10,
        "move_cooldown": 3,
        "attack_cooldown": 3,
        "lunge_cooldown": 2,
    }
    base.update(caps)
    return [
        {
            "x": 5, "y": 5, "facing": "east", "score": 0,
            "action": "attack", "thought": "", "visible": [],
            "capabilities": dict(base),
        }
        for _ in range(n)
    ]


# --------------------------------------------------------------------------
# 1. The actor's manual actually reaches the coach
# --------------------------------------------------------------------------

def test_eval_prompt_carries_the_actor_manual_verbatim():
    """Not a paraphrase and not a summary -- the same bytes the actor runs on."""
    prompt = ev.build_eval_prompt(_records(), "")
    assert get_skills_instruction() in prompt


def test_actor_manual_is_labelled_as_the_subordinate_s():
    """An unlabelled wall of rules reads as the coach's own instructions.

    The coach must be able to tell that this text is what its subordinate was
    told, so that it can judge whether the cat actually complied with it.
    """
    prompt = ev.build_eval_prompt(_records(), "")
    assert ev.ACTOR_MANUAL_BLOCK_HEADER in prompt
    assert "END OF YOUR SUBORDINATE'S MANUAL" in prompt


def test_manual_block_precedes_the_transcript():
    """Reference material first, then the thing to be judged against it."""
    prompt = ev.build_eval_prompt(_records(), "")
    assert prompt.index(ev.ACTOR_MANUAL_BLOCK_HEADER) < prompt.index("CAT TRANSCRIPT")


@pytest.mark.parametrize(
    "header_attr", ["ACTOR_MANUAL_BLOCK_HEADER", "CAPABILITIES_BLOCK_HEADER"]
)
def test_block_headers_match_the_names_used_in_eval_skills(header_attr):
    """The label in code and the label the manual refers to must be one string.

    eval_skills.md tells the coach what to do with each block by quoting its
    header. Renaming the constant without renaming the quote would leave the
    coach with instructions pointing at a block that no longer exists -- the
    same failure mode actor.py guards in test_prompt_is_data_only.py.
    """
    assert getattr(ev, header_attr) in EVAL_SKILLS.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 2. The coach's own manual must not restate the rules
# --------------------------------------------------------------------------

def test_coach_manual_does_not_describe_the_strike_zone_geometry():
    """The zone is specified in exactly one place, and this is not it."""
    text = EVAL_SKILLS.read_text(encoding="utf-8")
    body = text[text.index("## 2."):] if "## 2." in text else text

    assert "60°" not in body and "60 degree" not in body, (
        "the coach's manual is describing the slash as a cone again"
    )
    assert "row or column" not in body, (
        "the coach's manual is teaching exact-row alignment again; offaxis "
        "-1 and +1 both connect, so this discards two thirds of the zone"
    )


def test_coach_manual_does_not_republish_the_action_table():
    """Duplicated action lists are what drifted last time."""
    text = EVAL_SKILLS.read_text(encoding="utf-8")
    assert "| `lunge` |" not in text and "| `attack` |" not in text, (
        "the coach's manual has grown its own copy of the action table"
    )


def test_coach_manual_hardcodes_no_tunable_figures():
    """Every range and cooldown is live; a literal in prose cannot track it.

    Section 1 is exempt: it quotes the historical wrong values ("reach was 3
    when the engine said 2") to explain why this rule exists.
    """
    text = EVAL_SKILLS.read_text(encoding="utf-8")
    body = text[text.index("## 2."):]
    offenders = re.findall(
        r"(?:attack_range|vision_radius|\w+_cooldown)\s*(?:of|is|=|==)?\s*\d+", body
    )
    assert not offenders, f"tunables hardcoded in the coach's manual: {offenders}"


# --------------------------------------------------------------------------
# 3. The coach is given the live numbers it was previously missing
# --------------------------------------------------------------------------

def test_transcript_record_carries_capabilities():
    """Without this the coach has no authoritative figures at all."""
    from actors.cat import store

    rec = store._compact_record({
        "tick": 1,
        "observation": {
            "self": {"position": {"x": 1, "y": 2}, "facing": "north"},
            "capabilities": {"attack_range": 4, "move_cooldown": 3},
            "score": 0,
        },
        "response": {"action": "attack"},
    })
    assert rec["capabilities"] == {"attack_range": 4, "move_cooldown": 3}


def test_capabilities_block_states_the_live_values():
    block = ev.format_capabilities(_records(attack_range=4))
    assert ev.CAPABILITIES_BLOCK_HEADER in block
    assert "attack_range: 4" in block
    assert "move_cooldown: 3" in block


@pytest.mark.parametrize("reach", [1, 2, 3, 4, 7])
def test_strike_zone_predicate_is_derived_from_the_live_range(reach):
    """Depth is the value the coach has actually got wrong in play.

    Deriving the predicate from the capability means it cannot disagree with
    the engine no matter how the operator retunes.
    """
    block = ev.format_capabilities(_records(attack_range=reach))
    assert f"1 <= forward <= {reach}" in block
    assert "-1 <= offaxis <= 1" in block


def test_capabilities_block_warns_the_width_is_three():
    """The specific misconception that reached the cat, named explicitly."""
    block = ev.format_capabilities(_records(attack_range=4))
    assert "one tile off the centre line IS in the zone" in block.replace("  ", " ")


def test_retuning_mid_window_reports_the_latest_values():
    """The operator can retune between ticks; stale advice would be wrong."""
    records = _records(n=2, attack_range=2) + _records(n=2, attack_range=5)
    block = ev.format_capabilities(records)
    assert "attack_range: 5" in block
    assert "1 <= forward <= 5" in block
    assert "retuned" in block


def test_capabilities_block_is_absent_when_nothing_was_recorded():
    """Old transcripts predate the field; emit nothing rather than guess."""
    assert ev.format_capabilities([{"x": 1, "y": 1}]) == ""


def test_capabilities_override_is_stated_to_the_model():
    """The manual's printed figures are illustrations; say which wins."""
    block = ev.format_capabilities(_records())
    assert "override" in block.lower()


# --------------------------------------------------------------------------
# 4. The existing contracts still hold with the manual attached
# --------------------------------------------------------------------------

def test_no_tick_indices_leak_through_the_new_blocks():
    """The manual and capabilities blocks must not reintroduce tick numbers."""
    prompt = ev.build_eval_prompt(_records(), "")
    head = prompt[:prompt.index("CAT TRANSCRIPT")]
    assert not re.search(r"\btick \d+\b", head, re.IGNORECASE)


def test_actor_manual_path_is_the_actor_s_own():
    """Reading a second copy from a second path is how they diverge."""
    assert SKILLS_PATH.name == "actor_skills.md"
    assert SKILLS_PATH.exists()
