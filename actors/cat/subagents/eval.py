"""
Cat Evaluator — the long-horizon advisory subagent.

The Actor (``actors/cat/actor.py``) decides tick-by-tick off a short sliding window
and cannot perceive its own long-term behaviour. This subagent reads the Actor's
full transcript for the current game and writes standing orders back to it.

Handoff is deliberately file-based and one-directional:

    actor writes transcript.jsonl  ->  evaluator reads it
    evaluator writes coaching_notes.md  ->  actor reads it every tick

The API server pushes the transcript here directly on a tick interval. In
production that dispatch becomes a pub/sub publish; nothing in this module has to
change when it does, because the evaluator never reaches back into the Actor.
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from actors.cat import store
from actors.cat.actor import has_gemini_credentials, mark_auth_error

logger = logging.getLogger("cat_agent")

EVAL_MODEL = "gemini-2.5-flash"
EVAL_SKILLS_PATH = Path(__file__).parent / "eval_skills.md"

# How many ticks of play accumulate before an evaluation fires. Adjustable at
# runtime via the API server; this is only the starting value.
DEFAULT_EVAL_INTERVAL = 100

# Hard ceiling on how long a single evaluation may run. The evaluator is advisory,
# so a slow call must expire quietly rather than pile up behind the game clock.
EVAL_TIMEOUT_SECONDS = 60.0


def get_eval_instruction() -> str:
    """Loads the evaluator's own skills manual, distinct from the actor's."""
    if EVAL_SKILLS_PATH.exists():
        return EVAL_SKILLS_PATH.read_text(encoding="utf-8")
    return (
        "You are the Cat Evaluator, a long-horizon advisor to a Cat agent hunting "
        "mice on a discrete grid. Read the transcript and write short, concrete, "
        "directive markdown bullets advising the Cat what to do next."
    )


def _eval_instruction_provider(_ctx: ReadonlyContext) -> str:
    """Re-reads the manual on every run so edits take effect immediately.

    This used to be `instruction=get_eval_instruction()`, evaluated once when the
    module was imported. Because uvicorn's --reload only watches .py files, an
    edit to eval_skills.md changed nothing until some unrelated Python file
    happened to be touched. Worse, GET /api/agent/evaluator/skills reads the file
    directly, so the workbench displayed the new rules while the agent kept
    obeying the old ones -- the file looked applied when it was not.
    """
    return get_eval_instruction()


eval_agent = LlmAgent(
    name="cat_evaluator",
    model=EVAL_MODEL,
    instruction=_eval_instruction_provider,
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    ),
)
eval_session_service = InMemorySessionService()
eval_runner = Runner(
    agent=eval_agent,
    app_name="cat_evaluator_app",
    session_service=eval_session_service,
)

_eval_lock = asyncio.Lock()


def format_transcript(records: List[Dict[str, Any]]) -> str:
    """Renders transcript records as readable lines for the model.

    Deliberately emits NO absolute tick index.

    The Actor only ever sees a ~25-tick sliding window, so a directive citing
    "tick 26" refers to something it cannot look up; those citations are pure
    noise in its context. Telling the evaluator not to cite ticks helped but did
    not eliminate it -- the model kept reaching for the numbers because every
    line it read was labelled with one. Not showing it an index is deterministic
    where an instruction is merely probable.

    Ordering is preserved by line order, so runs, oscillations and durations
    remain fully analysable ("roughly 40 attacks", "for the last third").
    """
    lines = []
    for r in records:
        visible = r.get("visible") or []
        if visible:
            seen = ", ".join(
                f"{v.get('id')}@({v.get('x')},{v.get('y')})" for v in visible
            )
        else:
            seen = "nothing"
        action = (r.get("action") or "stay").upper()
        thought = r.get("thought") or ""
        # A "cooldown" row is a tick the cat was locked: it observed the board but
        # the engine discarded whatever it returned. Rendering these the same as
        # real moves made long runs of rejected commands look like the cat
        # stubbornly repeating itself, and the coach then scolded it for a choice
        # it never got to make.
        if r.get("tick_type") == "cooldown" or r.get("executed") is False:
            did = f"LOCKED (recovering from {r.get('locked_action') or 'previous action'}; no action possible)"
        else:
            did = f"did {action}"
        lines.append(
            f"at ({r.get('x')},{r.get('y')}) facing "
            f"{r.get('facing')} | score {r.get('score', 0)} | saw {seen} | {did}"
            + (f' | reasoning: "{thought}"' if thought else "")
        )
    return "\n".join(lines)


def _summarise_without_model(records: List[Dict[str, Any]]) -> str:
    """Offline advice used when no Gemini credentials are available.

    This keeps the reflexion loop observable during tests and local runs without
    credentials. It is intentionally thin: real advice is the model's job.
    """
    if not records:
        return "- **No Data**: No ticks recorded yet this game. Begin a broad sweep of the arena."

    xs = [r.get("x") for r in records if isinstance(r.get("x"), int)]
    ys = [r.get("y") for r in records if isinstance(r.get("y"), int)]
    cells = {(r.get("x"), r.get("y")) for r in records}
    facings = {r.get("facing") for r in records if r.get("facing")}

    bullets = [
        f"- **Coverage So Far**: Across {len(records)} ticks you occupied "
        f"{len(cells)} distinct cells."
    ]
    if xs and ys:
        bullets.append(
            f"- **Bounding Box**: Your movement stayed within x=[{min(xs)},{max(xs)}], "
            f"y=[{min(ys)},{max(ys)}]. Push beyond this box to reveal new ground."
        )
    unfaced = {"north", "south", "east", "west"} - facings
    if unfaced:
        bullets.append(
            f"- **Blind Directions**: You never faced {', '.join(sorted(unfaced))}. "
            "Your vision cone is forward-only, so those bearings are unobserved."
        )
    bullets.append(
        "- **Note**: Generated without model credentials; directives are positional only."
    )
    return "\n".join(bullets)


def build_eval_prompt(records: List[Dict[str, Any]], previous: str) -> str:
    """Assembles the full evaluator prompt.

    Extracted from evaluate() purely so it can be tested. While this was inline,
    mutation testing showed the tick range could be put back into the header
    with the entire suite still green: the tests could only reach
    format_transcript, and the header is the other route by which an absolute
    tick index reaches the model.
    """
    previous_block = ""
    if previous:
        previous_count = sum(
            1 for ln in previous.splitlines() if ln.strip().startswith("-")
        )
        # The wording here governs how the list evolves. It used to offer
        # only "keep / drop / replace" -- three operations that all
        # preserve cardinality -- so the list could never grow. Measured
        # live: feeding in 2 bullets produced 2-3 back, 6 produced 5.
        # Output tracked input almost exactly. Adding must be named as an
        # explicit, expected operation or it does not happen.
        previous_block = (
            "--- YOUR STANDING DIRECTIVES SO FAR ---\n"
            f"{previous}\n\n"
            "This is your accumulated dossier, not a draft to be rewritten. "
            "Assess whether the Cat actually followed each directive, then "
            "produce the updated dossier:\n"
            "  - CARRY FORWARD every directive that still holds, reproduced "
            "verbatim. This is the default; most directives should survive.\n"
            "  - DROP only a directive the Cat has demonstrably completed or "
            "that the transcript has proven wrong.\n"
            "  - SHARPEN a directive the Cat ignored, making it more concrete "
            "and easier to act on.\n"
            "  - ADD new directives for everything this stretch of transcript "
            "revealed that your dossier does not yet cover.\n\n"
            f"You currently have {previous_count} directive(s). The dossier is "
            "expected to grow as the game progresses and you learn more; a "
            "later evaluation covering more of the game should normally return "
            "more directives than an earlier one. Do not shrink it to hit some "
            "imagined length, and do not pad it to look thorough.\n\n"
        )

    # No tick indices reach the model -- not on the lines, not in this
    # header. See format_transcript() for why.
    return (
        f"{previous_block}"
        f"--- CAT TRANSCRIPT ({len(records)} consecutive ticks, "
        f"oldest first) ---\n"
        f"{format_transcript(records)}\n\n"
        "Write your standing orders for the Cat now, as markdown bullets. "
        "Do not refer to tick numbers; the Cat cannot see them."
    )


async def evaluate(records: List[Dict[str, Any]]) -> Tuple[str, str]:
    """Runs one evaluation pass over a transcript.

    Returns:
        (notes_markdown, source) where source is 'adk_gemini' or 'offline_fallback'.
    """
    if not records:
        return _summarise_without_model(records), "offline_fallback"

    # Skip live network calls during automated pytest runs unless explicitly enabled.
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        return _summarise_without_model(records), "offline_fallback"

    if not has_gemini_credentials():
        return _summarise_without_model(records), "offline_fallback"

    # NOTE: no tick bounds are computed here on purpose. They previously fed the
    # prompt header and were the last route by which an absolute tick index
    # reached the model. The published `tick_range` is derived separately in
    # evaluate_and_publish(), which is metadata for the UI, not model input.
    user_id = "cat_evaluator"
    session_id = "session_cat_evaluator"

    async with _eval_lock:
        try:
            # Each evaluation is independent; drop any prior session state.
            try:
                await eval_session_service.delete_session(
                    app_name="cat_evaluator_app", user_id=user_id, session_id=session_id
                )
            except Exception:
                pass
            await eval_session_service.create_session(
                app_name="cat_evaluator_app", user_id=user_id, session_id=session_id
            )

            prompt = build_eval_prompt(records, store.read_coaching_notes())

            msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            chunks: List[str] = []

            async with asyncio.timeout(EVAL_TIMEOUT_SECONDS):
                agen = eval_runner.run_async(
                    user_id=user_id, session_id=session_id, new_message=msg
                )
                try:
                    while True:
                        event = await agen.__anext__()
                        if event.error_code or event.error_message:
                            raise RuntimeError(
                                f"ADK Error: {event.error_code} - {event.error_message}"
                            )
                        if event.content and event.content.parts:
                            for p in event.content.parts:
                                if p.text and not getattr(p, "thought", False):
                                    chunks.append(p.text)
                except StopAsyncIteration:
                    pass

            notes = "".join(chunks).strip()
            if notes:
                return notes, "adk_gemini"
            logger.warning("Evaluator returned empty notes; using offline fallback.")

        except Exception as exc:
            err = str(exc)
            mark_auth_error(err)
            logger.warning("Evaluator execution failure: %s", err)

    return _summarise_without_model(records), "offline_fallback"


async def evaluate_and_publish() -> Dict[str, Any]:
    """Reads the current transcript, evaluates it, and publishes coaching notes.

    This is the entry point the API server dispatches to every N ticks. It owns the
    full read-evaluate-write cycle so the caller only has to fire and forget.
    """
    records = store.read_transcript_records()
    notes, source = await evaluate(records)

    tick_range = None
    if records:
        tick_range = f"{records[0].get('tick')}-{records[-1].get('tick')}"
    store.write_coaching_notes(notes, tick_range=tick_range)

    result = {
        "ticks_evaluated": len(records),
        "tick_range": tick_range,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    logger.info(
        "Evaluation published: %s ticks (%s) via %s",
        len(records),
        tick_range,
        source,
    )
    return result
