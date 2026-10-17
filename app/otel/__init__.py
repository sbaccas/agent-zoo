"""OpenTelemetry instrumentation, decorators, and SRE telemetry package for Cat & Mouse."""

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

__all__ = [
    "init_otel",
    "get_otel_config",
    "update_otel_config",
    "get_sre_store",
    "render_prometheus_metrics",
    "traced_span",
    "traced_genai",
    "traced_tool",
    "inject_traceparent",
    "extract_traceparent",
    "record_genai_metrics",
    "record_tool_metrics",
    "record_sim_tick_metrics",
]
