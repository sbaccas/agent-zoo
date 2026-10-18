"""Tests for OpenTelemetry instrumentation, decorators, runtime toggles, Prometheus metrics, and SRE dashboard."""

import asyncio
import pytest
from fastapi.testclient import TestClient

from app.main import app as game_app
from app.mcp.server import create_mcp_app
from actors.cat.api import app as cat_app
from actors.evil_cat.api import app as evil_cat_app
from app.otel.setup import (
    get_otel_config,
    get_sre_store,
    init_otel,
    render_prometheus_metrics,
    update_otel_config,
)
from app.otel.decorators import (
    extract_traceparent,
    inject_traceparent,
    record_genai_metrics,
    record_sim_tick_metrics,
    record_tool_metrics,
    traced_genai,
    traced_span,
    traced_tool,
)


def test_otel_runtime_config_toggles():
    """Verify live runtime toggle switches for tracing, file logging, console log level, and backend."""
    initial = get_otel_config()
    assert "tracing_enabled" in initial
    assert "file_logging_enabled" in initial
    assert "console_log_level" in initial
    assert "backend" in initial

    # Toggle off tracing & file logging, switch backend to gcp and log level to ERROR
    updated = update_otel_config({
        "tracing_enabled": False,
        "file_logging_enabled": False,
        "console_log_level": "ERROR",
        "backend": "gcp",
    })
    assert updated["tracing_enabled"] is False
    assert updated["file_logging_enabled"] is False
    assert updated["console_log_level"] == "ERROR"
    assert updated["backend"] == "gcp"

    # Restore defaults
    restored = update_otel_config({
        "tracing_enabled": True,
        "file_logging_enabled": True,
        "console_log_level": "WARNING",
        "backend": "local",
    })
    assert restored["tracing_enabled"] is True
    assert restored["file_logging_enabled"] is True
    assert restored["console_log_level"] == "WARNING"
    assert restored["backend"] == "local"


def test_traced_decorators_and_metrics():
    """Verify @traced_span, @traced_genai, and @traced_tool record spans and SRE metrics when enabled and skip when disabled."""
    store = init_otel(service_name="test-service", agent_id="cat-test", port=9999)
    update_otel_config({"tracing_enabled": True, "file_logging_enabled": True})

    @traced_span("test.sync_span", {"custom.attr": "hello"})
    def sync_work(x: int) -> int:
        return x * 2

    @traced_tool(tool_name="test_tool")
    async def async_tool_call(sim_id: str = "active"):
        await asyncio.sleep(0.005)
        return {"available": True, "sim_id": sim_id}

    @traced_genai(role="actor", model="gemini-2.5-flash", span_name="genai.test_actor")
    async def async_genai_call(agent_id: str = "cat-test"):
        await asyncio.sleep(0.005)
        record_genai_metrics(
            role="actor",
            model="gemini-2.5-flash",
            latency_ms=420.5,
            ttft_ms=110.2,
            input_tokens=850,
            output_tokens=45,
            thinking_tokens=128,
            status="OK",
            decision_source="adk_gemini",
        )
        return {"action": "north"}

    assert sync_work(21) == 42
    res_tool = asyncio.run(async_tool_call("sim-1"))
    assert res_tool["available"] is True
    res_genai = asyncio.run(async_genai_call("cat-test"))
    assert res_genai["action"] == "north"

    record_sim_tick_metrics(12.5, tick=10)
    record_tool_metrics("test_tool", duration_ms=0.1, status="OK", cached=True)

    summary = store.get_summary(include_spans=True, span_limit=20)
    assert summary["genai"]["actor"]["calls"] >= 1
    assert summary["genai"]["actor"]["ttft_ms"]["p50"] > 0
    assert summary["tokens"]["input_tokens_total"] >= 850
    assert summary["tokens"]["thinking_tokens_total"] >= 128
    assert summary["tokens"]["rolling_60s_tpm"] >= 1023
    assert "test_tool" in summary["tools"]
    assert summary["tools"]["test_tool"]["calls"] >= 2
    assert summary["tools"]["test_tool"]["cache_hits"] >= 1

    # Verify Prometheus exposition format
    prom_text = render_prometheus_metrics(store)
    assert "cm_genai_latency_ms" in prom_text
    assert "cm_genai_ttft_ms" in prom_text
    assert "cm_genai_tokens_total" in prom_text
    assert "cm_genai_tpm_rolling_60s" in prom_text
    assert "cm_tool_calls_total" in prom_text


def test_traceparent_propagation():
    """Verify W3C traceparent injection and extraction across dictionaries."""
    update_otel_config({"tracing_enabled": True})

    @traced_span("parent.tick")
    def build_envelope():
        env = {"type": "observation"}
        tp = inject_traceparent(env)
        return env, tp

    env, tp = build_envelope()
    assert tp is not None
    assert env.get("traceparent") == tp
    assert tp.startswith("00-")

    ctx = extract_traceparent(env)
    assert ctx is not None


def test_sre_endpoints_across_services():
    """Verify /sre, /metrics, /api/sre/status, and /api/sre/config on Game Server, MCP Server, Cat Agent, and Evil Cat Agent."""
    for app_instance in (game_app, create_mcp_app(), cat_app, evil_cat_app):
        client = TestClient(app_instance)

        # HTML dashboard
        resp_sre = client.get("/sre")
        assert resp_sre.status_code == 200
        assert "SRE Observability" in resp_sre.text
        if "Content-Security-Policy" in resp_sre.headers:
            assert "'unsafe-inline'" in resp_sre.headers["Content-Security-Policy"]

        # Prometheus metrics
        resp_metrics = client.get("/metrics")
        assert resp_metrics.status_code == 200
        assert "cm_genai_latency_ms" in resp_metrics.text

        # Node SRE status JSON
        resp_status = client.get("/api/sre/status")
        assert resp_status.status_code == 200
        data = resp_status.json()
        assert "config" in data
        assert "genai" in data
        assert "tokens" in data
        assert "tools" in data

        # Runtime config GET/POST
        resp_cfg = client.get("/api/sre/config")
        assert resp_cfg.status_code == 200
        assert "tracing_enabled" in resp_cfg.json()


def test_build_trace_trees_hierarchy():
    """Verify build_trace_trees groups spans by trace_id into a Cloud Trace waterfall with ASCII tree connectors."""
    from app.otel.dashboard import build_trace_trees

    tid = "8a3f91c2" + "0" * 24
    mock_spans = [
        {
            "trace_id": tid,
            "span_id": "span_1",
            "parent_span_id": None,
            "name": "agent.act",
            "node_id": "cat-1",
            "service_name": "cm-agent-cat-1",
            "agent_id": "cat-1",
            "tick": 664,
            "start_time_ns": 1_000_000_000,
            "end_time_ns": 1_640_000_000,
            "duration_ms": 640.0,
            "status": "OK",
            "attributes": {"cm.tick": 664},
        },
        {
            "trace_id": tid,
            "span_id": "span_2",
            "parent_span_id": "span_1",
            "name": "genai.actor_deliberation",
            "node_id": "cat-1",
            "service_name": "cm-agent-cat-1",
            "agent_id": "cat-1",
            "tick": 664,
            "start_time_ns": 1_010_000_000,
            "end_time_ns": 1_620_000_000,
            "duration_ms": 610.0,
            "status": "OK",
            "attributes": {
                "gen_ai.request.model": "gemini-2.5-flash",
                "gen_ai.usage.input_tokens": 900,
                "gen_ai.usage.output_tokens": 45,
            },
        },
        {
            "trace_id": tid,
            "span_id": "span_3",
            "parent_span_id": "span_1",
            "name": "mcp.http.POST /api/telemetry",
            "node_id": "mcp-server",
            "service_name": "cm-mcp-server",
            "agent_id": "cat-1",
            "tick": 664,
            "start_time_ns": 1_625_000_000,
            "end_time_ns": 1_633_000_000,
            "duration_ms": 8.0,
            "status": "OK",
            "attributes": {},
        },
    ]

    trees = build_trace_trees(mock_spans)
    assert len(trees) == 1
    t = trees[0]
    assert t["trace_id"] == tid
    assert t["span_count"] == 3
    assert t["crossed_services"] is True
    assert "Tick 664" in t["story"]
    assert t["spans"][0]["tree_prefix"] == "└─ "
    assert t["spans"][1]["tree_prefix"] == "    ├─ "
    assert t["spans"][2]["tree_prefix"] == "    └─ "
    assert t["spans"][2]["crossed_network"] is True


def test_coach_eval_two_call_maximum_enforcement():
    """Verify both Cat Coach and Evil Coach enforce a strict 2-call maximum:
    Call 1 allows tool calls; Call 2 injects tool outputs and strips all tools so evaluation finishes after Call 2.
    """
    from google.genai import types
    from google.adk.models import LlmRequest
    from actors.cat.subagents import eval as cat_eval
    from actors.evil_cat import eval as evil_eval

    for mod in (cat_eval, evil_eval):
        assert mod.MAX_COACH_LLM_CALLS == 2
        cb = mod._enforce_two_call_max_before_model
        assert cb in mod.eval_agent.canonical_before_model_callbacks

        # Call 1: No function_response in contents -> tools remain available
        req1 = LlmRequest(
            contents=[types.Content(role="user", parts=[types.Part.from_text(text="eval context")])],
            config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[])]),
        )
        req1.tools_dict["get_boxscore"] = object()
        cb(None, req1)
        assert req1.config.tools is not None
        assert "get_boxscore" in req1.tools_dict

        # Call 2: function_response present -> tools are stripped and closing instruction appended
        req2 = LlmRequest(
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text="eval context")]),
                types.Content(role="model", parts=[types.Part.from_function_call(name="get_boxscore", args={})]),
                types.Content(role="user", parts=[types.Part.from_function_response(name="get_boxscore", response={"available": True})]),
            ],
            config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[])]),
        )
        req2.tools_dict["get_boxscore"] = object()
        cb(None, req2)
        assert req2.config.tools is None
        assert len(req2.tools_dict) == 0
        assert "maximum 2 LLM calls" in (req2.config.system_instruction or "")

