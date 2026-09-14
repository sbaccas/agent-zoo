"""Tick-rate control: sub-1 presets, and a dropdown that tells the truth.

Two separate defects are covered here.

1. The dropdown bottomed out at 1 tick/sec. Measured live, Gemini takes
   1.6-2.2s per decision, so a 1000ms tick budget still loses the race and the
   Cat falls back to the System-1 reflex heuristic. The transcript bore this
   out: 86.1% heuristic_reflex vs 13.9% adk_gemini. Letting the model actually
   drive requires a budget at least as long as its latency.

2. The dropdown was write-only. app.js read speedSelect.value on change and
   never wrote it back, so the control displayed whichever <option> carried the
   hardcoded `selected` attribute. A server running at 5 ticks/sec showed
   "10 ticks / sec", and reloading did not fix it.

The JS half is exercised by actually executing app.js's syncSpeedSelect in
JavaScriptCore against a fake <select>, rather than pattern-matching source.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app/static/index.html"
APP_JS = ROOT / "app/static/app.js"
STYLES = ROOT / "app/static/styles.css"

JSC = Path(
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
)

# Gemini's measured decision latency, in seconds. A tick budget shorter than
# this means the model cannot answer before the tick is resolved.
GEMINI_LATENCY_SECONDS = 1.6


def _option_values():
    html = INDEX_HTML.read_text()
    block = re.search(
        r'<select id="speedSelect".*?</select>', html, re.DOTALL
    )
    assert block, "the speedSelect dropdown is gone from index.html"
    return [float(v) for v in re.findall(r'value="([\d.]+)"', block.group(0))]


def test_dropdown_offers_a_rate_slow_enough_for_the_model_to_answer():
    """At least one preset must give Gemini a full decision window.

    Without this the 'let the model drive' mode is not reachable from the UI at
    all: every selectable rate resolves the tick before a reply can arrive.
    """
    values = _option_values()
    budgets = [1.0 / v for v in values]
    assert any(b >= GEMINI_LATENCY_SECONDS for b in budgets), (
        f"no tick rate gives Gemini its measured {GEMINI_LATENCY_SECONDS}s; "
        f"slowest option is {max(budgets):.2f}s per tick"
    )


def test_dropdown_still_offers_fast_rates_for_throughput():
    """Slow rates are for watching the model; fast rates are for volume.

    Guards against someone 'fixing' the latency problem by deleting the fast
    presets, which would make long heuristic runs painful to generate.
    """
    values = _option_values()
    assert max(values) >= 25, "the high-throughput tick rates were removed"


def test_dropdown_values_are_within_the_backend_clamp():
    """set_tick_rate clamps to [0.1, 100.0]; an option outside that lies.

    Selecting 0.05 would silently run at 0.1 and the UI would keep claiming
    0.05, which is the same class of bug as the write-only dropdown below.
    """
    for v in _option_values():
        assert 0.1 <= v <= 100.0, f"option {v} falls outside the backend clamp"


def test_default_selected_option_matches_the_backend_default():
    """The pre-selected option is what the user sees before any state arrives.

    SimulationConfig.tick_rate defaults to 5.0, so anything else here is wrong
    on first paint.
    """
    html = INDEX_HTML.read_text()
    selected = re.search(r'<option value="([\d.]+)" selected>', html)
    assert selected, "no option is marked selected"
    assert float(selected.group(1)) == 5.0, (
        "the pre-selected tick rate no longer matches SimulationConfig's default of 5.0"
    )


def test_update_ui_syncs_the_dropdown_from_server_state():
    """The dropdown must be driven by state, not just read from.

    Comment lines are stripped first. Mutation testing caught this: commenting
    the call out left the substring intact inside `// syncSpeedSelect(...)`, so
    a naive `in` check passed against dead code.
    """
    live = "\n".join(
        ln for ln in APP_JS.read_text().splitlines()
        if not ln.strip().startswith("//")
    )
    assert "syncSpeedSelect(speedSelect, state.tick_rate)" in live, (
        "updateUI no longer syncs the tick-rate dropdown from server state"
    )


def test_speed_hint_uses_a_defined_css_variable():
    """--text-muted is referenced in this stylesheet but never defined in :root.

    A var() that resolves to nothing silently inherits, which previously
    produced unreadable text. Pin the hint to a token that actually exists.
    """
    css = STYLES.read_text()
    hint = re.search(r"\.speed-hint\s*\{[^}]*\}", css, re.DOTALL)
    assert hint, ".speed-hint styling is missing"
    used = re.findall(r"var\((--[\w-]+)\)", hint.group(0))
    root = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL).group(1)
    for var in used:
        assert f"{var}:" in root, f".speed-hint uses undefined CSS variable {var}"


def test_static_assets_are_cachebusted_past_their_old_versions():
    """Stale JS is indistinguishable from a broken fix in the browser."""
    html = INDEX_HTML.read_text()
    js_v = int(re.search(r"app\.js\?v=(\d+)", html).group(1))
    css_v = int(re.search(r"styles\.css\?v=(\d+)", html).group(1))
    assert js_v >= 16, f"app.js cachebuster is stale at v={js_v}"
    assert css_v >= 12, f"styles.css cachebuster is stale at v={css_v}"


# --------------------------------------------------------------------------
# Behavioural tests: execute the real syncSpeedSelect in JavaScriptCore.
# --------------------------------------------------------------------------

jsc_only = pytest.mark.skipif(
    not JSC.exists(), reason="JavaScriptCore helper not available on this platform"
)


def _extract_sync_fn() -> str:
    src = APP_JS.read_text()
    start = src.index("function syncSpeedSelect(")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("could not brace-match syncSpeedSelect in app.js")


HARNESS = """
// Minimal <select> stand-in. Mirrors the DOM semantics the function relies on:
// options list, value assignment, appendChild, and remove(). Real elements
// created via document.createElement carry remove(), so appended options must
// get one here too or the cleanup path cannot be exercised.
function makeSelect(values, current) {
  const sel = {
    options: [],
    value: current,
    appendChild(o) {
      o.remove = function () {
        const i = sel.options.indexOf(o);
        if (i >= 0) sel.options.splice(i, 1);
      };
      sel.options.push(o);
    },
  };
  values.forEach(v => sel.appendChild({ value: v, dataset: {} }));
  return sel;
}
var document = {
  activeElement: null,
  createElement: function() { return { value: '', textContent: '', dataset: {} }; },
};
var window = {};

%s

var out = [];

// 1. server rate differs from displayed rate -> adopt the server's value
var s1 = makeSelect(['1','5','10'], '10');
syncSpeedSelect(s1, 5);
out.push(['adopts_server_rate', s1.value]);

// 2. sub-1 rate selects cleanly (0.5 must stringify to an existing option)
var s2 = makeSelect(['0.25','0.5','1','5'], '5');
syncSpeedSelect(s2, 0.5);
out.push(['handles_sub_one', s2.value]);

// 3. user has the menu focused -> do not fight them
var s3 = makeSelect(['1','5','10'], '10');
document.activeElement = s3;
syncSpeedSelect(s3, 5);
document.activeElement = null;
out.push(['respects_focus', s3.value]);

// 4. out-of-band rate with no matching preset -> surface it, never mislabel
var s4 = makeSelect(['1','5','10'], '10');
syncSpeedSelect(s4, 3);
out.push(['custom_rate_value', s4.value]);
out.push(['custom_rate_count', String(s4.options.length)]);

// 5. returning to a preset clears the stale custom option
syncSpeedSelect(s4, 5);
out.push(['after_return_value', s4.value]);
out.push(['after_return_count', String(s4.options.length)]);

// 6. junk input must not blank the control
var s6 = makeSelect(['1','5'], '5');
syncSpeedSelect(s6, undefined);
syncSpeedSelect(s6, 0);
syncSpeedSelect(s6, NaN);
out.push(['ignores_junk', s6.value]);

print(JSON.stringify(out));
"""


@jsc_only
def _run_js():
    script = HARNESS % _extract_sync_fn()
    proc = subprocess.run(
        [str(JSC), "-e", script], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"jsc failed: {proc.stderr}\n{proc.stdout}"
    return dict(json.loads(proc.stdout.strip().splitlines()[-1]))


@jsc_only
def test_sync_speed_select_behaviour():
    r = _run_js()

    assert r["adopts_server_rate"] == "5", (
        "the dropdown ignored the server's rate -- this is the original "
        "write-only bug, where the UI showed 10 while the sim ran at 5"
    )
    assert r["handles_sub_one"] == "0.5", "sub-1 rates do not round-trip"
    assert r["respects_focus"] == "10", (
        "the dropdown was rewritten while the user had it open"
    )


@jsc_only
def test_sync_speed_select_surfaces_out_of_band_rates():
    r = _run_js()
    assert r["custom_rate_value"] == "3", (
        "a rate set outside the dropdown was not surfaced; the control would "
        "display a rate that is not in effect"
    )
    assert r["custom_rate_count"] == "4", "the custom option was not appended"


@jsc_only
def test_sync_speed_select_cleans_up_custom_options():
    r = _run_js()
    assert r["after_return_value"] == "5"
    assert r["after_return_count"] == "3", (
        "stale custom options accumulate in the dropdown across rate changes"
    )


@jsc_only
def test_sync_speed_select_ignores_junk_input():
    r = _run_js()
    assert r["ignores_junk"] == "5", (
        "a missing or nonsensical tick_rate blanked the control"
    )
