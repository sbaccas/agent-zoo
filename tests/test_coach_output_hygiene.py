"""The coach must publish directives, not its scratchpad.

On 2026-09-19 the Cat's standing orders were a 70-line dossier: a full box-score
dump, an "Analysis of Existing Directives" section, a chain-of-thought aside
("Wait, `slash_attempts` is 15 ... This is a crucial finding!"), and then the
directive list twice over -- once with `*` bullets and once with `-`, welded
together mid-line. The Actor reads this file every tick as standing orders, so
every one of those words was competing with the actual instructions.

The model was not disobeying. eval_skills.md 3 says "Write only the bullets.
Nothing else.", and the final turn complied. The fault was in collection:
`evaluate` appended `p.text` from *every* event and `"".join`-ed them, so
narration emitted alongside the `get_boxscore` call was welded onto the answer.

It stayed invisible while the evaluator's thinking budget was 0 -- an unthinking
model answers in one turn, so there was only ever one chunk. Raising the budget
to 4096 made the model plan, call the tool, and narrate around it. Sixteen
archived notes from before that change are all 4-7 clean lines; the one after is
70 lines of dossier.

Two defences, tested here:

  1. `_is_final_event`      -- drop turns that carry a tool call or are partial
  2. `_strip_to_directives` -- drop narration sharing a turn with the answer
"""

import pytest

from actors.cat.subagents.eval import _is_final_event, _strip_to_directives


# --------------------------------------------------------------------------
# _strip_to_directives
# --------------------------------------------------------------------------

CLEAN = """\
- **Strict Strike Zone Calculation**: Your `slash_accuracy` is 80%. Before \
attacking, ensure `forward` is between 1 and 4 and `offaxis` between -1 and 1.
- **Convert All Strike Zone Opportunities**: You had 15 `zone_chances` but \
declined 4 of them.
- **Maximize Multi-Kills**: You achieved 4 multi-kills. Check for all mice in \
the zone, not just the primary target."""


# Abridged from the real 2026-09-19 failure, preserving its structure: prose
# heading, box score rendered *as a bullet list*, numbered analysis, a bolded
# heading, then the directives. The box-score-as-bullets detail is why the
# stripper cannot simply anchor on the first bullet in the document.
POLLUTED = """\
The box score provides the following for `cat-1`:
- `kills`: 16
- `slash_accuracy`: 0.8 (meaning 80% hits, 20% misses)
- `whiffs`: 3 (consistent with 15 * 0.2 = 3 misses)
- `zone_declined`: 4 (15 total chances - 4 declined = 11 attempts. Wait, \
`slash_attempts` is 15. This is a crucial finding!)

**Analysis of Existing Directives:**

1.  **Verify Strike Range**: The transcript showed 3 whiffs from incorrect \
`forward` calculations.
    *   **Evaluation:** This directive is still relevant and needs sharpening.

**Revised Directives:**

- **Strict Strike Zone Calculation**: Your `slash_accuracy` is 80%. Before \
attacking, ensure `forward` is between 1 and 4.
- **Convert All Strike Zone Opportunities**: You had 15 `zone_chances` but \
declined 4 of them."""


def test_clean_output_is_passed_through_untouched():
    """The common case must not be damaged by the defence.

    Every one of the sixteen archived notes looks like this. A stripper that
    "cleaned" them would be a worse bug than the one it fixes.
    """
    assert _strip_to_directives(CLEAN) == CLEAN


def test_the_real_dossier_is_reduced_to_its_directives():
    out = _strip_to_directives(POLLUTED)

    assert "The box score provides" not in out
    assert "Analysis of Existing Directives" not in out
    assert "crucial finding" not in out
    assert "Revised Directives" not in out
    assert "`kills`: 16" not in out

    assert out.startswith("- **Strict Strike Zone Calculation**")
    assert "Convert All Strike Zone Opportunities" in out
    assert len(out.splitlines()) == 2


def test_a_wrapped_directive_does_not_sever_the_list():
    """Continuation lines are indented, and must not read as prose.

    Without the indent guard the second directive's wrapped line would look like
    a fresh prose paragraph, and everything above it -- including the first
    directive -- would be discarded.
    """
    text = (
        "- **First**: keep this one.\n"
        "- **Second**: this directive wraps\n"
        "  onto a second line that is indented.\n"
        "- **Third**: and this one too."
    )
    out = _strip_to_directives(text)
    assert out == text
    assert "First" in out and "Third" in out


def test_prose_only_output_is_kept_rather_than_erased():
    """Fail safe. Badly formatted coaching still beats no coaching.

    An empty reply falls through to `_summarise_without_model`, whose positional
    filler then overwrites the real standing orders -- the evaluator's signature
    failure mode. Returning imperfect text is strictly better.
    """
    prose = "I could not identify any clear improvements this window."
    assert _strip_to_directives(prose) == prose


def test_a_trailing_prose_line_does_not_erase_everything():
    """If nothing follows the last prose line, keep the whole reply.

    Otherwise a model that signs off after its list ("Let me know if you'd like
    more.") would publish an empty file.
    """
    text = "- **Only directive**: do the thing.\nThat concludes my analysis."
    out = _strip_to_directives(text)
    assert "Only directive" in out


@pytest.mark.parametrize("marker", ["-", "*", "+", "1.", "2)"])
def test_every_markdown_list_marker_is_recognised(marker):
    """The observed failure switched from `*` to `-` between turns.

    Recognising only one marker style would make the defence depend on which
    style the model happened to choose.
    """
    text = f"Here are my notes:\n{marker} **Directive**: do the thing."
    out = _strip_to_directives(text)
    assert out == f"{marker} **Directive**: do the thing."


def test_empty_input_is_handled():
    assert _strip_to_directives("") == ""


# --------------------------------------------------------------------------
# _is_final_event
# --------------------------------------------------------------------------

class _FakeEvent:
    def __init__(self, final):
        self._final = final

    def is_final_response(self):
        return self._final


class _ExplodingEvent:
    def is_final_response(self):
        raise RuntimeError("ADK changed its mind about this API")


def test_the_answer_turn_is_recognised():
    assert _is_final_event(_FakeEvent(True)) is True


def test_a_tool_call_turn_is_not_the_answer():
    """This is the whole fix.

    `is_final_response()` is false for a turn carrying a function call, which is
    the turn the coach narrated in.
    """
    assert _is_final_event(_FakeEvent(False)) is False


def test_an_unrecognised_event_fails_open_as_narration():
    """Degrade to the old behaviour rather than losing the coaching channel.

    If ADK's API shifts under us, every event reads as narration, `final_chunks`
    ends up empty, and `evaluate` falls back to joining everything. That is the
    pre-fix output: cluttered, but present and logged -- never silently blank.
    """
    assert _is_final_event(_ExplodingEvent()) is False
    assert _is_final_event(object()) is False
