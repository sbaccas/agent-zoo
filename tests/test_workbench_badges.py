"""Tests the workbench's decision-source badge logic by executing the real JS.

Fix #4 lives entirely in `workbench.js`, so a Python-only suite cannot cover it.
The user's report was specifically that ticks #1077-#1081 read
`Fallback (Auth Error)` in one frame and `ADK Gemini` in the next, with no
change to the underlying data -- a pure rendering defect.

`node` is not installed on this machine, so we drive JavaScriptCore instead.
`describeSource` is extracted from the file and evaluated in isolation, which
also enforces the property that matters: it must be a PURE function of the
recorded source, taking no dependency on live auth state.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

JSC = Path(
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
)
WORKBENCH_JS = Path(__file__).resolve().parents[1] / "actors/cat/static/workbench.js"

pytestmark = pytest.mark.skipif(
    not JSC.exists(), reason="JavaScriptCore helper not available on this platform"
)


def _extract_describe_source() -> str:
    """Pulls the describeSource function body out of workbench.js by brace matching."""
    src = WORKBENCH_JS.read_text()
    start = src.index("function describeSource(")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("could not brace-match describeSource in workbench.js")


def _run_js(snippet: str) -> str:
    fn = _extract_describe_source()
    program = f"{fn}\n{snippet}"
    proc = subprocess.run(
        [str(JSC), "-e", program], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise AssertionError(f"jsc failed: {proc.stderr or proc.stdout}")
    return proc.stdout.strip()


def test_describe_source_exists_and_is_extractable():
    assert "switch" in _extract_describe_source()


@pytest.mark.parametrize("source,expected_label", [
    ("adk_gemini", "ADK Gemini"),
    ("manual_override", "Override"),
    ("heuristic_reflex", "Reflex (System 1)"),
    ("heuristic", "Heuristic"),
    ("heuristic_fallback", "Fallback"),
])
def test_each_source_maps_to_its_own_label(source, expected_label):
    out = _run_js(f"print(describeSource('{source}', null).label);")
    assert out == expected_label


def test_fallback_is_never_labelled_gemini():
    """The exact user-reported symptom, stated as an invariant."""
    for source in ("heuristic_fallback", "heuristic_reflex", "heuristic"):
        out = _run_js(f"print(describeSource('{source}', null).label);")
        assert "Gemini" not in out, (
            f"source {source!r} rendered as {out!r}; a heuristic tick must never "
            f"claim it came from the model"
        )


def test_describe_source_ignores_live_auth_state():
    """The root cause, pinned directly.

    The old predicate was `(source === 'adk_gemini' || hasAuth)`. If anything
    like it returns, `describeSource` will start reading globals. Here `hasAuth`
    is defined as true in the surrounding scope; a correct implementation is a
    pure function of its arguments and must ignore it entirely.
    """
    snippet = (
        "var hasAuth = true;"
        "var cachedState = { has_auth: true, auth_valid: true };"
        "print(describeSource('heuristic_fallback', null).label);"
    )
    assert _run_js(snippet) == "Fallback", (
        "describeSource consulted live auth state; healthy credentials will "
        "retroactively relabel historical fallback ticks as ADK Gemini"
    )


def test_reflex_is_visually_distinct_from_error_fallback():
    """A System-1 reflex is a healthy fast path and must not look like a failure."""
    reflex = _run_js("print(describeSource('heuristic_reflex', null).chip);")
    failure = _run_js("print(describeSource('heuristic_fallback', null).chip);")
    assert reflex != failure
    assert failure == "fallback"


def test_fallback_reason_is_surfaced_in_the_tooltip():
    # repr() emits a double-quoted literal here (the reason contains a quote),
    # which is valid JS.
    reason = "model chose 'attack', which is not legal this tick"
    out = _run_js(f"print(describeSource('heuristic_fallback', {reason!r}).title);")
    assert out == reason


def test_unknown_source_degrades_gracefully():
    out = _run_js("print(describeSource('some_future_engine', null).label);")
    assert out == "some_future_engine"


def test_missing_source_does_not_crash():
    out = _run_js("print(describeSource(undefined, null).label);")
    assert out == "Unknown"


def test_no_badge_site_consults_auth_when_labelling_a_recorded_tick():
    """Static guard: the original bug pattern must not reappear in live code.

    Comment lines are stripped first -- describeSource's docstring deliberately
    quotes the old predicate to explain what went wrong.
    """
    code_lines = [
        line for line in WORKBENCH_JS.read_text().splitlines()
        if not line.lstrip().startswith("//")
    ]
    offenders = re.findall(
        r"===\s*'adk_gemini'\s*\|\|\s*hasAuth", "\n".join(code_lines)
    )
    assert not offenders, (
        f"found {len(offenders)} occurrence(s) of the `|| hasAuth` relabelling "
        f"predicate that caused AI-04"
    )


def test_workbench_cachebuster_matches_no_stale_version():
    """Static files changed, so the cachebuster must have moved off v20."""
    index = WORKBENCH_JS.parent / "index.html"
    content = index.read_text()
    match = re.search(r"workbench\.js\?v=(\d+)", content)
    assert match, "no cachebuster found on workbench.js"
    assert int(match.group(1)) >= 21, (
        "cachebuster not bumped; browsers will serve the cached buggy script"
    )
