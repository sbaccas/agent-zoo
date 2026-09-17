"""The order of fields in CatTacticalDecision is behaviour, not formatting.

Structured output is generated one token at a time in declaration order, and
the actor runs with thinking_budget=0, so no scratchpad exists anywhere else.
Whichever field comes first is decided with an empty context.

When ``action`` was declared first, the cat named its move before computing
anything and ``thought`` degraded into a justification it could not retract.
Measured over 211 recorded attack decisions: forward/offaxis arithmetic correct
94.7% of the time, but only 19% of swings had a mouse in the strike zone, and
23.5% live slash accuracy against a heuristic rival's 100%. Transcripts contain
the contradiction verbatim (tick 134: "A mouse is in the strike zone; attacking
to score. forward: 8, offaxis: 2. Not in strike zone, repositioning for a
better shot." with action == attack).

Replaying one fixed position 10 times per schema, changing nothing but field
order: action-first chose an out-of-range attack 10/10, thought-first 1/10.

These tests exist because the swap looks cosmetic and reads like something to
tidy back into "action first" for readability.
"""

from actors.models import CatTacticalDecision


def test_thought_is_declared_before_action():
    fields = list(CatTacticalDecision.model_fields.keys())
    assert fields.index("thought") < fields.index("action"), (
        "thought must precede action: the model emits fields in declaration "
        "order with no thinking budget, so action-first means choosing before "
        "reasoning. See this module's docstring for the measured impact."
    )


def test_serialised_schema_preserves_that_order():
    """Pydantic order only matters if it survives into the wire schema.

    This is what ADK hands to Gemini as response_schema, and what the model
    fills in top to bottom.
    """
    properties = list(CatTacticalDecision.model_json_schema()["properties"])
    assert properties.index("thought") < properties.index("action")


def test_model_dump_emits_thought_first():
    decision = CatTacticalDecision(
        thought="forward 4, offaxis 2 - outside the -1..1 strike zone",
        action="north",
        target_mouse_id="mouse-83",
        strategy="STALKING",
    )
    assert list(decision.model_dump())[:2] == ["thought", "action"]


def test_construction_is_keyword_only_so_the_swap_is_safe():
    """Guards the swap itself: positional construction would silently invert.

    Pydantic models reject positional arguments, so no existing call site can
    have been relying on (action, thought) ordering.
    """
    try:
        CatTacticalDecision("north", "some reasoning")
    except TypeError:
        return
    raise AssertionError(
        "CatTacticalDecision accepted positional args; reordering fields "
        "could silently swap action and thought at a call site."
    )
