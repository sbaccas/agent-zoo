"""No tick index may reach the evaluator model.

The Actor sees only a ~25-tick sliding window, so a directive citing "tick 26"
points at something it cannot look up. Instructing the evaluator not to cite
ticks reduced this but did not eliminate it -- measured live at 2 citations in a
7-bullet dossier -- because every transcript line it read was labelled with a
tick number.

These tests pin the deterministic half of the fix: the model is never shown an
index in the first place.
"""

import re

import pytest

from actors.cat.subagents.eval import (
    build_eval_prompt,
    format_transcript,
    get_eval_instruction,
)

# "Tick 26:", "tick 26", "ticks 40-95", "ticks 15, 16, 23"
TICK_INDEX = re.compile(r"\bticks?\s+\d+", re.IGNORECASE)


def _records(n=40, start=101):
    return [
        {
            "tick": start + i,
            "x": 20 + (i % 5),
            "y": 51 + (i % 3),
            "facing": ["north", "east", "south", "west"][i % 4],
            "visible": [{"id": "mouse-343", "x": 22, "y": 52}] if i % 2 else [],
            "action": "attack" if i % 3 == 0 else "east",
            "thought": "exploring territory",
            "score": 0,
        }
        for i in range(n)
    ]


def test_transcript_contains_no_tick_indices():
    out = format_transcript(_records())
    found = TICK_INDEX.findall(out)
    assert not found, (
        f"transcript leaks tick indices to the model: {found[:5]}. "
        f"The Actor cannot see those ticks, so any directive quoting them is noise."
    )


def test_transcript_does_not_leak_the_raw_tick_values():
    """Guards against a format change that prints the number without the word."""
    recs = _records(n=5, start=9001)
    out = format_transcript(recs)
    for r in recs:
        assert str(r["tick"]) not in out, (
            f"tick value {r['tick']} appears verbatim in the transcript"
        )


def test_assembled_prompt_contains_no_tick_indices():
    """The whole prompt, not just the transcript lines.

    Mutation testing caught this gap: putting "ticks {first} to {last}" back
    into the prompt header left every other test in this file green, because
    they all stop at format_transcript. The header is the second route by
    which an absolute index can reach the model, so it needs its own guard.
    """
    prompt = build_eval_prompt(_records(), previous="")
    found = TICK_INDEX.findall(prompt)
    assert not found, f"the assembled prompt leaks tick indices: {found[:5]}"


def test_assembled_prompt_does_not_leak_raw_tick_values():
    """Catches a header that prints the bounds as bare numbers."""
    recs = _records(n=5, start=9001)
    prompt = build_eval_prompt(recs, previous="")
    for r in recs:
        assert str(r["tick"]) not in prompt, (
            f"tick value {r['tick']} appears verbatim in the assembled prompt"
        )


def test_assembled_prompt_is_clean_with_previous_notes_too():
    """The previous-notes block is model input as well.

    Carried-forward directives are reproduced verbatim, so if an old dossier
    contained citations they would be echoed back in. This asserts the prompt
    scaffolding adds none of its own; it cannot sanitise the Cat's history.
    """
    prompt = build_eval_prompt(_records(n=3), previous="- **Head East**: cross x=11.")
    assert "YOUR STANDING DIRECTIVES SO FAR" in prompt
    assert not TICK_INDEX.findall(prompt)


def test_prompt_tells_the_model_not_to_cite_ticks():
    """The instruction is belt-and-braces alongside simply not showing them."""
    prompt = build_eval_prompt(_records(n=3), previous="")
    assert "tick numbers" in prompt.lower()


def test_prompt_growth_framing_names_adding_as_an_operation():
    """The 'keep / drop / replace' framing is what pinned the list at three.

    All three of those verbs preserve cardinality. ADD has to be named as an
    expected operation or the dossier cannot grow no matter what the manual says.
    """
    prompt = build_eval_prompt(_records(n=3), previous="- **A**: x\n- **B**: y")
    assert "ADD" in prompt, "the prompt no longer invites the dossier to grow"
    assert "CARRY FORWARD" in prompt
    assert "2 directive(s)" in prompt, "the current directive count is not reported"


def test_transcript_still_preserves_everything_needed_for_analysis():
    """Removing ticks must not cost the evaluator its actual signal."""
    recs = _records(n=6, start=200)
    out = format_transcript(recs)
    lines = out.splitlines()

    # One line per tick, in order: sequence is still fully recoverable.
    assert len(lines) == len(recs)
    assert "(20,51)" in lines[0]          # position
    assert "facing north" in lines[0]     # facing
    assert "score 0" in lines[0]          # score, for hit/miss inference
    assert "ATTACK" in lines[0]           # chosen action
    assert "exploring territory" in lines[0]   # reasoning
    assert "mouse-343@(22,52)" in lines[1]     # what it saw


def test_transcript_ordering_is_oldest_first():
    recs = _records(n=3, start=1)
    recs[0]["x"], recs[1]["x"], recs[2]["x"] = 1, 2, 3
    lines = format_transcript(recs).splitlines()
    assert lines[0].startswith("at (1,")
    assert lines[2].startswith("at (3,")


def test_eval_manual_forbids_tick_citations():
    manual = get_eval_instruction()
    assert "Never cite tick numbers" in manual, (
        "the evaluator manual no longer forbids tick citations"
    )


def _output_format_examples():
    """Return the fenced example blocks from the manual's Output Format section.

    The blocks must be isolated rather than scanning the whole manual, because
    the rule that bans citations necessarily *quotes* citations in its own
    "write this, not that" translation table. Those quotations are the lesson,
    not a violation of it. Only the worked examples are copied verbatim by the
    model, so only they need to be clean.
    """
    manual = get_eval_instruction()
    _, _, tail = manual.partition("## 3. Output Format")
    assert tail, "the manual no longer has an Output Format section to check"
    blocks = re.findall(r"```(.*?)```", tail, re.DOTALL)
    assert len(blocks) >= 2, (
        "expected a good example and a labelled bad example in Output Format, "
        f"found {len(blocks)} fenced block(s)"
    )
    return blocks


def test_eval_manual_good_example_does_not_demonstrate_tick_citation():
    """The worked example used to do the exact thing the rule forbids.

    It literally read "Ticks 40-95 oscillated between (3,4) and (3,5)". Models
    copy examples more reliably than they follow prose, so a correct rule sat
    beside a contradicting example is worse than no rule at all.
    """
    good_block = _output_format_examples()[0]
    offenders = TICK_INDEX.findall(good_block)
    assert not offenders, (
        f"the evaluator's positive example demonstrates tick citation: {offenders}"
    )


def test_eval_manual_keeps_a_labelled_bad_example():
    """Guards the contrast, not just the absence.

    Showing the banned phrasing under an explicit "this is unusable" label is
    what makes the rule concrete. If that block is ever deleted the good-example
    test above would still pass while the manual got weaker, so pin it here.
    """
    bad_block = _output_format_examples()[1]
    assert TICK_INDEX.findall(bad_block), (
        "the counter-example no longer demonstrates the banned phrasing, so the "
        "contrast that teaches the rule has been lost"
    )


@pytest.mark.parametrize("permitted", [
    "Commit to a single heading for at least 6 consecutive ticks before reassessing.",
    "Your score is still 0 despite roughly 40 attacks.",
    "You wasted 50 ticks bumping the same wall.",
])
def test_forward_durations_are_not_flagged_as_citations(permitted):
    """Durations stay legal: the Cat can count those off going forward.

    Only the backward tick *index* is unusable. A blanket ban on the word would
    have cost real directives.
    """
    assert not TICK_INDEX.findall(permitted)


def test_format_transcript_omits_locked_reasoning_and_labels_reflex_fallbacks():
    """Locked cooldown ticks must not emit unused reflex thoughts, and executed reflex fallbacks must not be labelled as model reasoning."""
    recs = [
        {
            "tick": 1007,
            "x": 99,
            "y": 99,
            "facing": "south",
            "visible": [],
            "action": "west",
            "thought": "Exploring territory",
            "source": "heuristic_reflex",
            "tick_type": "cooldown",
            "locked_action": "lunge",
            "executed": False,
            "score": 96,
        },
        {
            "tick": 1008,
            "x": 99,
            "y": 99,
            "facing": "south",
            "visible": [],
            "action": "north",
            "thought": "Exploring territory",
            "source": "heuristic_reflex",
            "tick_type": "decision",
            "executed": True,
            "score": 96,
        },
    ]
    lines = format_transcript(recs).splitlines()
    assert "LOCKED" in lines[0]
    assert "Exploring territory" not in lines[0]
    assert 'reflex_fallback (not model reasoning): "Exploring territory"' in lines[1]
    assert ' | reasoning: "Exploring territory"' not in lines[1]
