"""The coach can measure outcomes, not just describe behaviour.

The evaluator reads the Actor's transcript, which records what the Cat chose and
nothing about what those choices were worth. It also has no view of `cat-2` at
all: the heuristic cat is driven by the engine and never writes a transcript. So
the coach could only ever reissue qualitative corrections, and it did -- "Verify
Attack Conditions" appears near-verbatim in two consecutive note generations,
because nothing told the coach the directive had already been given and had
already failed to move anything.

`get_boxscore` is the read path onto the engine's own counters. These tests pin
the properties that make it usable for that job:

  * both cats come back, because the rival's row is the benchmark that turns a
    bare figure into evidence;
  * a rate with an empty denominator stays `null`, because a fabricated 0.0
    reads as a measured failure;
  * an unreachable game server produces an explicit flag, because the one thing
    worse than no statistics is invented ones;
  * and no test in this suite dials :8000, for the same reason
    CAT_AGENT_AUTO_CONNECT exists.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from actors.cat.subagents import eval as ev


# --------------------------------------------------------------------------
# Fixtures and fakes
# --------------------------------------------------------------------------

def _cat_row(cat_id, **overrides):
    """One box score row, shaped exactly as app/game/stats.py emits it."""
    row = {
        "id": cat_id,
        "mode": "ws" if cat_id == "cat-1" else "auto",
        "alive": True,
        "kills": 9,
        "kills_per_100_ticks": 3.46,
        "tiles_per_kill": 9.44,
        "slash_attempts": 11,
        "slash_accuracy": 0.818,
        "whiffs": 2,
        "multi_kills": 0,
        "zone_chances": 9,
        "zone_conversion": 0.889,
        "zone_declined": 1,
        "tiles_run": 85,
        "unique_tiles": 62,
        "ground_covered": 0.006,
        "speed_tiles_per_tick": 0.327,
        "ticks_per_tile": 4.19,
        "moves": 69,
        "lunges": 8,
        "lunge_share": 0.104,
        "wall_bonks": 0,
        "ticks_free": 151,
        "ticks_locked": 249,
        "locked_pct": 0.642,
        "prey_in_sight_pct": 0.613,
    }
    row.update(overrides)
    return row


def _payload(**overrides):
    body = {
        "simulation_id": "sim-x",
        "status": "running",
        "tick": 260,
        "round": 32,
        "tracking_since_tick": 0,
        "arena": {"width": 100, "height": 100, "tiles": 10000},
        "leader": "cat-2",
        "cats": [
            _cat_row("cat-1", lunge_share=0.104, zone_conversion=0.583),
            _cat_row("cat-2", lunge_share=0.130, zone_conversion=1.0),
        ],
    }
    body.update(overrides)
    return body


class _Response:
    """Minimal stand-in for httpx.Response; only what the tool touches."""

    def __init__(self, status_code=200, payload=None, json_raises=None):
        self.status_code = status_code
        self._payload = payload
        self._json_raises = json_raises

    def json(self):
        if self._json_raises is not None:
            raise self._json_raises
        return self._payload


@pytest.fixture
def allow_fetch(monkeypatch):
    """Opt this test back into the HTTP path, against a mocked transport.

    The tool refuses to open a socket under pytest by default. Every test that
    exercises the request itself has to say so explicitly, which is what keeps
    an un-mocked test from silently reaching the live game server.
    """
    monkeypatch.setenv(ev.BOXSCORE_FETCH_ENV, "1")


def _format_mcp_response(name: str, **response_kwargs):
    status_code = response_kwargs.get("status_code", 200)
    json_raises = response_kwargs.get("json_raises")
    payload = response_kwargs.get("payload")

    if json_raises:
        return {"available": False, "reason": f"ValueError: {json_raises}"}
    if status_code == 403:
        return {"available": False, "reason": "disclosure is disabled"}
    if status_code != 200:
        return {"available": False, "reason": f"game server returned HTTP {status_code}"}
    if payload is None or not isinstance(payload, dict):
        return {"available": False, "reason": "game server returned an unexpected payload"}
    cats = payload.get("cats")
    if name == "get_boxscore" and (not isinstance(cats, list) or not cats):
        return {"available": False, "reason": "game server reported no cats"}

    if name == "get_coverage_map":
        return {
            "available": True,
            "simulation_id": payload.get("simulation_id"),
            "tick": payload.get("tick"),
            "ascii": payload.get("ascii"),
            "arena": payload.get("arena"),
            "grid_size": payload.get("grid_size"),
            "cell_size": payload.get("cell_size"),
            "cats": cats,
        }

    tick = payload.get("tick")
    since = payload.get("tracking_since_tick")
    measured = tick - since if isinstance(tick, int) and isinstance(since, int) else None
    res = {
        "available": True,
        "simulation_id": payload.get("simulation_id"),
        "status": payload.get("status"),
        "tick": tick,
        "round": payload.get("round"),
        "tracking_since_tick": since,
        "ticks_measured": measured,
        "arena": payload.get("arena"),
        "leader": payload.get("leader"),
        "cats": cats,
    }
    for k in ("mice_alive", "mice_initial", "captures"):
        if k in payload:
            res[k] = payload[k]
    return res


def _patch_get(**response_kwargs):
    """Patch the MCPClient call the tool makes, returning the mock for assertions."""
    async def _mock_call(tool_name: str, arguments=None):
        return _format_mcp_response(tool_name, **response_kwargs)

    mock = AsyncMock(side_effect=_mock_call)
    return patch("app.mcp.client.MCPClient.call_tool", new=mock), mock


# --------------------------------------------------------------------------
# 1. Both cats, because the rival is the benchmark
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_boxscore_returns_a_row_for_every_cat(allow_fetch):
    """Filtering to cat-1 would leave the coach a number and no yardstick.

    The teaching signal is the gap between the two rows, not either row on its
    own: the heuristic converts essentially every strike-zone chance while the
    LLM cat converts a little over half. A coach that cannot see the rival can
    only say "convert more", which is the advice that has already failed.
    """
    patcher, _ = _patch_get(payload=_payload())
    with patcher:
        result = await ev.get_boxscore()

    assert result["available"] is True
    assert [c["id"] for c in result["cats"]] == ["cat-1", "cat-2"]


@pytest.mark.asyncio
async def test_each_cat_keeps_its_own_measurements(allow_fetch):
    """Rows must not be merged, averaged or deduplicated on the way through."""
    patcher, _ = _patch_get(payload=_payload())
    with patcher:
        result = await ev.get_boxscore()

    rows = {c["id"]: c for c in result["cats"]}
    assert rows["cat-1"]["zone_conversion"] == 0.583
    assert rows["cat-2"]["zone_conversion"] == 1.0
    assert rows["cat-1"]["lunge_share"] != rows["cat-2"]["lunge_share"]


@pytest.mark.asyncio
async def test_every_kpi_field_survives_the_round_trip(allow_fetch):
    """The tool is a read path, not a projection.

    Dropping a field here would silently remove a KPI from the coach's reach,
    and the only symptom would be advice that never mentions it.
    """
    patcher, _ = _patch_get(payload=_payload())
    with patcher:
        result = await ev.get_boxscore()

    assert set(result["cats"][0]) == set(_cat_row("cat-1"))


# --------------------------------------------------------------------------
# 2. Nulls are data; zeros would be a lie
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_null_rates_survive_instead_of_becoming_zero(allow_fetch):
    """A rate with an empty denominator means "not measured", not "scored 0".

    Nine of them are null on a fresh game. A null slash accuracy means the Cat
    has not swung at all; coercing it to 0.0 would present that to the coach as
    a cat that swung and missed every time, which is a different problem with
    opposite advice.
    """
    fresh = _cat_row(
        "cat-1",
        kills=0,
        kills_per_100_ticks=None,
        tiles_per_kill=None,
        slash_accuracy=None,
        zone_conversion=None,
        speed_tiles_per_tick=None,
        ticks_per_tile=None,
        lunge_share=None,
        locked_pct=None,
        prey_in_sight_pct=None,
    )
    patcher, _ = _patch_get(payload=_payload(cats=[fresh, _cat_row("cat-2")]))
    with patcher:
        result = await ev.get_boxscore()

    row = result["cats"][0]
    for field in (
        "kills_per_100_ticks", "tiles_per_kill", "slash_accuracy",
        "zone_conversion", "speed_tiles_per_tick", "ticks_per_tile",
        "lunge_share", "locked_pct", "prey_in_sight_pct",
    ):
        assert row[field] is None, f"{field} was invented as {row[field]!r}"


# --------------------------------------------------------------------------
# 3. How much of the game was actually measured
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_measurement_window_is_reported(allow_fetch):
    """A tracker attached mid-game reports partial counters.

    Without the window the coach reads a half-measured game as a bad one, and
    writes directives against rates that were never computed over the whole
    match.
    """
    patcher, _ = _patch_get(payload=_payload(tick=260, tracking_since_tick=100))
    with patcher:
        result = await ev.get_boxscore()

    assert result["tick"] == 260
    assert result["tracking_since_tick"] == 100
    assert result["ticks_measured"] == 160


@pytest.mark.asyncio
async def test_measurement_window_is_none_when_the_clock_is_missing(allow_fetch):
    """An older or partial server response must not produce a bogus span."""
    patcher, _ = _patch_get(payload=_payload(tick=None, tracking_since_tick=None))
    with patcher:
        result = await ev.get_boxscore()

    assert result["ticks_measured"] is None


# --------------------------------------------------------------------------
# 4. Failure is explicit, never fabricated
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connection_error_returns_a_flag_rather_than_raising(allow_fetch):
    """The coach is advisory; a dead game server must not fail the evaluation.

    Raising here would propagate into evaluate(), which catches broadly and
    falls back to the offline summariser -- so the whole set of notes would be
    lost over a statistic that is optional by design.
    """
    mock = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with patch("app.mcp.client.MCPClient.call_tool", new=mock):
        result = await ev.get_boxscore()

    assert result["available"] is False
    assert "ConnectError" in result["reason"]


@pytest.mark.asyncio
async def test_timeout_returns_a_flag_rather_than_hanging(allow_fetch):
    """A wedged server costs a few seconds, not the evaluation budget."""
    mock = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
    with patch("app.mcp.client.MCPClient.call_tool", new=mock):
        result = await ev.get_boxscore()

    assert result["available"] is False
    assert "ReadTimeout" in result["reason"]


@pytest.mark.asyncio
async def test_non_200_is_unavailable_and_names_the_status(allow_fetch):
    """404 is what a stale or finished simulation id looks like."""
    patcher, _ = _patch_get(status_code=404, payload={"detail": "not found"})
    with patcher:
        result = await ev.get_boxscore()

    assert result["available"] is False
    assert "404" in result["reason"]


@pytest.mark.asyncio
async def test_unparseable_body_is_unavailable(allow_fetch):
    """A proxy or error page in place of JSON must not reach the coach."""
    patcher, _ = _patch_get(json_raises=ValueError("not json"))
    with patcher:
        result = await ev.get_boxscore()

    assert result["available"] is False


@pytest.mark.asyncio
async def test_a_response_with_no_cats_is_unavailable(allow_fetch):
    """An empty roster is absence of measurement, not a game of zeroes."""
    patcher, _ = _patch_get(payload=_payload(cats=[]))
    with patcher:
        result = await ev.get_boxscore()

    assert result["available"] is False


@pytest.mark.asyncio
async def test_the_unavailable_payload_carries_no_statistics(allow_fetch):
    """Nothing numeric may ride along with a failure.

    A partially-populated failure shape is how a zero gets quoted as a
    measurement: the coach sees a familiar key and reads it as data.
    """
    mock = AsyncMock(side_effect=httpx.ConnectError("boom"))
    with patch("app.mcp.client.MCPClient.call_tool", new=mock):
        result = await ev.get_boxscore()

    assert set(result) == {"available", "reason"}
    assert isinstance(result["reason"], str) and result["reason"]


# --------------------------------------------------------------------------
# 5. Test isolation: the suite must not dial the live game server
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_request_is_made_when_the_env_guard_is_set(monkeypatch):
    """`CAT_AGENT_BOXSCORE_FETCH=0` switches the socket off entirely.

    Same convention and same reason as CAT_AGENT_AUTO_CONNECT: a test run and a
    live workbench share a host, and a coach reaching :8000 would pull whatever
    game happens to be playing into the assertions.
    """
    monkeypatch.setenv(ev.BOXSCORE_FETCH_ENV, "0")
    mock = AsyncMock(return_value=_Response(payload=_payload()))
    with patch("app.mcp.client.MCPClient.call_tool", new=mock):
        result = await ev.get_boxscore()

    mock.assert_not_awaited()
    assert result["available"] is False
    assert ev.BOXSCORE_FETCH_ENV in result["reason"]


@pytest.mark.asyncio
async def test_pytest_alone_is_enough_to_suppress_the_request(monkeypatch):
    """The default under pytest is off, so a new test cannot leak a call.

    Opting in has to be deliberate. If the guard only worked when conftest set
    it, any test module that ran without it would quietly hit the network.
    """
    monkeypatch.delenv(ev.BOXSCORE_FETCH_ENV, raising=False)
    assert "PYTEST_CURRENT_TEST" in __import__("os").environ

    mock = AsyncMock(return_value=_Response(payload=_payload()))
    with patch("app.mcp.client.MCPClient.call_tool", new=mock):
        result = await ev.get_boxscore()

    mock.assert_not_awaited()
    assert result["available"] is False


def test_the_guard_is_readable_without_running_the_tool(monkeypatch):
    """The predicate is public so a caller can explain the empty result."""
    monkeypatch.setenv(ev.BOXSCORE_FETCH_ENV, "0")
    assert ev._boxscore_fetch_enabled() is False
    monkeypatch.setenv(ev.BOXSCORE_FETCH_ENV, "1")
    assert ev._boxscore_fetch_enabled() is True


# --------------------------------------------------------------------------
# 6. Where it points, and how long it waits
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_request_targets_the_active_simulation(allow_fetch):
    """The coach evaluates the game in progress; the server resolves 'active'."""
    patcher, mock = _patch_get(payload=_payload())
    with patcher:
        await ev.get_boxscore()

    assert mock.await_args.args[0] == "get_boxscore"
    assert mock.await_args.args[1] == {"sim_id": "active"}


@pytest.mark.asyncio
async def test_the_base_url_is_configurable(allow_fetch, monkeypatch):
    """The MCP server is a separate process and configurable via CAT_MCP_SERVER_URL."""
    monkeypatch.setenv("CAT_MCP_SERVER_URL", "http://mcp.internal:9111/")
    from app.mcp.client import MCPClient
    client = MCPClient()
    assert client.base_url == "http://mcp.internal:9111"


@pytest.mark.asyncio
async def test_the_default_base_url_is_the_local_game_server(allow_fetch, monkeypatch):
    monkeypatch.delenv("CAT_MCP_SERVER_URL", raising=False)
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    from app.mcp.client import MCPClient
    client = MCPClient()
    assert client.base_url == "http://127.0.0.1:9050"


def test_the_timeout_is_short_relative_to_the_evaluation_budget():
    """Note generation must not be held open by an unreachable game server."""
    assert 0 < ev.BOXSCORE_TIMEOUT_SECONDS <= 5.0
    assert ev.BOXSCORE_TIMEOUT_SECONDS < ev.EVAL_TIMEOUT_SECONDS / 10


# --------------------------------------------------------------------------
# 7. The tool is actually wired to the coach
# --------------------------------------------------------------------------

def test_the_tool_is_registered_on_the_evaluator_agent():
    """A perfect tool the agent was never given is a no-op."""
    assert ev.get_boxscore in ev.eval_agent.tools


def test_adk_derives_a_named_declaration_from_the_function():
    """ADK builds the declaration from the signature and docstring.

    Asserted rather than assumed: the model can only call a tool that appears in
    its declarations, and a rename of the function silently renames the tool.
    """
    tools = asyncio.run(ev.eval_agent.canonical_tools())
    by_name = {t.name: t for t in tools}
    assert "get_boxscore" in by_name
    assert by_name["get_boxscore"].description.strip(), "the tool has no description"


def test_the_evaluator_has_no_output_schema():
    """An output_schema disables tool calling in ADK.

    The agent has never had one, but adding one later would silently switch the
    box score off while leaving every other test here green.
    """
    assert ev.eval_agent.output_schema is None


def test_the_tool_takes_no_arguments():
    """Nothing for the model to get wrong, and nothing for it to hallucinate.

    The coach only ever evaluates the game in progress, so a simulation id
    parameter would be a field the model could fill in with an id that does not
    exist -- and `thinking_budget=0` is not the setting to gamble that on.
    """
    import inspect

    assert not inspect.signature(ev.get_boxscore).parameters


# --------------------------------------------------------------------------
# 8. The data-only rule, applied to the tool
# --------------------------------------------------------------------------

# From tests/test_prompt_is_data_only.py: Python emits facts and delimiters,
# markdown emits every imperative. The tool's payload and its declaration are
# both model-facing, so both are bound by that rule.
BANNED = (
    "you must", "you should", "prioritize", "prioritise", "make sure",
    "remember to", "tactical rule", "is bad", "is poor", "too low", "too high",
    "needs to", "improve", "worse than", "better than", "severity", "verdict",
)


def _strings(value):
    """Every string anywhere in a nested structure, including keys."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out += _strings(k) + _strings(v)
        return out
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    return []


@pytest.mark.asyncio
@pytest.mark.parametrize("banned", BANNED)
async def test_the_returned_payload_gives_no_instructions(allow_fetch, banned):
    """The payload is numbers and plain labels; interpretation is the manual's.

    A verdict baked in here would sit closer to the decision than
    eval_skills.md does and would quietly outrank it -- the exact failure the
    `TACTICAL RULE:` line caused on the actor side.
    """
    patcher, _ = _patch_get(payload=_payload())
    with patcher:
        result = await ev.get_boxscore()

    for text in _strings(result):
        assert banned not in text.lower(), f"the box score payload instructs: {text!r}"


@pytest.mark.parametrize("banned", BANNED)
def test_the_tool_declaration_gives_no_tactical_instructions(banned):
    """The docstring is what the model reads when deciding to call the tool.

    Describing what comes back is legitimate and necessary. Telling the model
    what to do about the numbers is not: that belongs in eval_skills.md, where
    it can be edited without a code change and where it cannot outrank the rest
    of the manual.
    """
    assert banned not in (ev.get_boxscore.__doc__ or "").lower()


@pytest.mark.asyncio
async def test_the_unavailable_reason_states_a_cause_not_a_judgement(allow_fetch):
    """The reason is diagnostic text for the coach, not advice."""
    mock = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with patch("app.mcp.client.MCPClient.call_tool", new=mock):
        result = await ev.get_boxscore()

    for banned in BANNED:
        assert banned not in result["reason"].lower()


# --------------------------------------------------------------------------
# 9. The imperatives live in the manual, and they are all there
# --------------------------------------------------------------------------

def _manual():
    return ev.EVAL_SKILLS_PATH.read_text(encoding="utf-8").lower()


def test_the_manual_names_the_tool_exactly_as_the_model_sees_it():
    """A manual that refers to a tool by a stale name instructs nothing."""
    assert "get_boxscore" in _manual()


def test_the_manual_requires_the_tool_before_notes_are_written():
    manual = _manual()
    assert "call it before you write your notes" in manual


def test_the_manual_requires_comparison_against_the_rival():
    """A bare figure is not evidence; the rival's figure is what makes it one."""
    manual = _manual()
    assert "rival" in manual
    assert "benchmark against the rival" in manual


def test_the_manual_requires_closing_the_loop_on_previous_directives():
    """This is the specific failure the whole change exists to fix.

    "Verify Attack Conditions" was reissued near-verbatim across consecutive
    generations. The manual has to demand that a carried-forward directive
    reports whether its KPI actually moved, or the tool just adds numbers to the
    same repetition.
    """
    manual = _manual()
    assert "close the loop" in manual
    assert "did not move" in manual


def test_the_manual_forbids_inventing_figures_when_the_tool_is_unavailable():
    manual = _manual()
    assert "available: false" in manual
    assert "invent nothing" in manual


def test_the_manual_explains_that_rates_are_fractions_and_nulls_are_not_zero():
    """Both are ways a correct payload gets reported as a wrong number."""
    manual = _manual()
    assert "fractions" in manual
    assert "null" in manual


# --------------------------------------------------------------------------
# 10. Population counts in boxscore payload & get_coverage_map tool
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_boxscore_carries_population_counts_when_disclosed(allow_fetch):
    payload = _payload(mice_alive=142, mice_initial=150, captures=8)
    patcher, _ = _patch_get(payload=payload)
    with patcher:
        result = await ev.get_boxscore()

    assert result["available"] is True
    assert result["mice_alive"] == 142
    assert result["mice_initial"] == 150
    assert result["captures"] == 8


@pytest.mark.asyncio
async def test_coverage_map_tool_returns_grid_and_ascii(allow_fetch):
    payload = {
        "simulation_id": "sim-x",
        "tick": 100,
        "arena": {"width": 100, "height": 100},
        "grid_size": 5,
        "cell_size": 20,
        "cats": [{"id": "cat-1", "ascii": "MAP"}],
        "ascii": "FULL ASCII MAP",
    }
    patcher, _ = _patch_get(payload=payload)
    with patcher:
        result = await ev.get_coverage_map()

    assert result["available"] is True
    assert result["ascii"] == "FULL ASCII MAP"
    assert result["grid_size"] == 5
    assert result["cell_size"] == 20


@pytest.mark.asyncio
async def test_coverage_map_tool_handles_disabled_disclosure(allow_fetch):
    mock = AsyncMock(return_value={"available": False, "reason": "coverage map disclosure is disabled"})
    with patch("app.mcp.client.MCPClient.call_tool", new=mock):
        result = await ev.get_coverage_map()

    assert result["available"] is False
    assert "disclosure is disabled" in result["reason"]


def test_eval_agent_has_both_tools():
    tool_names = [getattr(t, "__name__", None) or getattr(t, "name", None) for t in ev.eval_agent.tools]
    assert "get_boxscore" in tool_names
    assert "get_coverage_map" in tool_names


@pytest.mark.asyncio
async def test_boxscore_strips_cat_positions(allow_fetch, monkeypatch):
    """Box score must not reveal individual cat positions so location deception remains viable."""
    from actors.evil_cat import eval as evil_ev

    monkeypatch.setenv(evil_ev.BOXSCORE_FETCH_ENV, "1")
    payload = _payload(
        cats=[
            _cat_row("cat-1", position={"x": 10, "y": 20}),
            _cat_row("cat-2", position={"x": 80, "y": 90}),
            _cat_row("cat-3", position={"x": 50, "y": 50}),
        ]
    )
    patcher, _ = _patch_get(payload=payload)
    with patcher:
        cat_res = await ev.get_boxscore()
        evil_res = await evil_ev.get_boxscore()

    assert all("position" not in c for c in cat_res["cats"])
    assert all("position" not in c for c in evil_res["cats"])


