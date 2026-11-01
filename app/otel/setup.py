"""OpenTelemetry SDK configuration, destination exporter switch, and SRE telemetry store.

Supports a single-line / single-env-var destination switch (`CM_OTEL_BACKEND`):
  - `local` (default): In-memory SRE ring buffer + optional JSONL file sink + Prometheus `/metrics` endpoint
  - `otlp`: Exports spans & metrics to `OTEL_EXPORTER_OTLP_ENDPOINT` (e.g. Jaeger / Grafana Tempo / Collector)
  - `gcp`: Exports to Google Cloud Trace / Google Cloud Managed Service for Prometheus (or OTLP `telemetry.googleapis.com`)
"""

from collections import deque
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import statistics
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Sequence
import urllib.request

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)


# ---------------------------------------------------------------------------
# Runtime Toggle Configuration (Live-tunable via API or Environment Variables)
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class OTelRuntimeConfig:
    """Live-tunable runtime switches for OTel tracing, disk JSONL logging, and console log level."""

    def __init__(self) -> None:
        self.tracing_enabled: bool = _env_bool("CM_OTEL_TRACING", True)
        self.file_logging_enabled: bool = _env_bool("CM_OTEL_FILE_LOGGING", False)
        self.console_log_level: str = (os.environ.get("CM_LOG_LEVEL") or "WARNING").upper()
        self.backend: str = (os.environ.get("CM_OTEL_BACKEND") or "local").lower()
        self.otlp_endpoint: str = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or ""
        self.gcp_project: str = os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
        self.last_updated_at: float = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tracing_enabled": self.tracing_enabled,
            "file_logging_enabled": self.file_logging_enabled,
            "console_log_level": self.console_log_level,
            "backend": self.backend,
            "otlp_endpoint": self.otlp_endpoint,
            "gcp_project": self.gcp_project,
            "last_updated_at": self.last_updated_at,
        }


_CONFIG = OTelRuntimeConfig()
_CONFIG_LOCK = threading.Lock()


def get_otel_config() -> Dict[str, Any]:
    with _CONFIG_LOCK:
        return _CONFIG.to_dict()


def is_tracing_enabled() -> bool:
    return _CONFIG.tracing_enabled


def is_file_logging_enabled() -> bool:
    return _CONFIG.file_logging_enabled


def update_otel_config(
    tracing_enabled: Optional[Any] = None,
    file_logging_enabled: Optional[bool] = None,
    console_log_level: Optional[str] = None,
    backend: Optional[str] = None,
    otlp_endpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """Updates runtime OTel/logging settings and applies log level changes immediately."""
    if isinstance(tracing_enabled, dict):
        payload = tracing_enabled
        tracing_enabled = payload.get("tracing_enabled")
        file_logging_enabled = payload.get("file_logging_enabled", file_logging_enabled)
        console_log_level = payload.get("console_log_level", console_log_level)
        backend = payload.get("backend", backend)
        otlp_endpoint = payload.get("otlp_endpoint", otlp_endpoint)

    with _CONFIG_LOCK:
        if tracing_enabled is not None:
            _CONFIG.tracing_enabled = bool(tracing_enabled)
        if file_logging_enabled is not None:
            _CONFIG.file_logging_enabled = bool(file_logging_enabled)
        if console_log_level is not None:
            lvl_str = str(console_log_level).upper().strip()
            if lvl_str in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                _CONFIG.console_log_level = lvl_str
                lvl = getattr(logging, lvl_str, logging.WARNING)
                logging.getLogger().setLevel(lvl)
                for logger_name in ("uvicorn", "uvicorn.error", "simulation.events", "cat_mcp_server"):
                    logging.getLogger(logger_name).setLevel(lvl)
        if backend is not None and backend.lower() in ("local", "otlp", "gcp"):
            _CONFIG.backend = backend.lower()
        if otlp_endpoint is not None:
            _CONFIG.otlp_endpoint = str(otlp_endpoint).strip()
        _CONFIG.last_updated_at = time.time()
        return _CONFIG.to_dict()


# ---------------------------------------------------------------------------
# Structured Log Filter (Injects trace_id, span_id, service_name)
# ---------------------------------------------------------------------------


class OTelLogCorrelationFilter(logging.Filter):
    """Attaches OpenTelemetry trace_id and span_id to every Python LogRecord."""

    def __init__(self, service_name: str, agent_id: str = "") -> None:
        super().__init__()
        self.service_name = service_name
        self.agent_id = agent_id

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
            record.otel_trace_id = f"{ctx.trace_id:032x}"
            record.otel_span_id = f"{ctx.span_id:016x}"
        else:
            record.otel_trace_id = "0" * 32
            record.otel_span_id = "0" * 16
        record.otel_service = self.service_name
        record.otel_agent_id = self.agent_id
        return True


# ---------------------------------------------------------------------------
# In-Memory SRE Store (Spans, GenAI TTFT/Tokens, Tool Calls, Sim Ticks, Logs)
# ---------------------------------------------------------------------------


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((pct / 100.0) * (len(s) - 1)))))
    return round(float(s[idx]), 2)


class SRETelemetryStore:
    """Thread-safe in-memory store for OTel spans, GenAI metrics, tool calls, and correlated logs."""

    def __init__(self, service_name: str = "cm-service", agent_id: str = "", max_spans: int = 1500) -> None:
        self.service_name = service_name
        self.agent_id = agent_id
        self.start_time = time.time()
        self._lock = threading.Lock()

        # Rolling spans & correlated log entries
        self.spans: Deque[Dict[str, Any]] = deque(maxlen=max_spans)
        # Dedicated priority buffer for multi-step GenAI, Coach, MCP, A2A, and error spans
        # so high-frequency 500ms tick spans never evict slower multi-server traces.
        self.priority_spans: Deque[Dict[str, Any]] = deque(maxlen=max_spans)
        self.logs: Deque[Dict[str, Any]] = deque(maxlen=250)

        # GenAI sliding window events
        self.genai_events: Deque[Dict[str, Any]] = deque(maxlen=500)
        self.genai_totals: Dict[str, Any] = {
            "calls_total": 0,
            "actor_calls": 0,
            "coach_calls": 0,
            "interrogator_calls": 0,
            "input_tokens_total": 0,
            "output_tokens_total": 0,
            "thinking_tokens_total": 0,
            "errors_429_total": 0,
            "errors_timeout_total": 0,
            "errors_other_total": 0,
            "decision_sources": {},
        }

        # Tool call telemetry
        self.tool_events: Deque[Dict[str, Any]] = deque(maxlen=400)
        self.tool_stats: Dict[str, Dict[str, Any]] = {}
        self.tool_cache_hits_total: int = 0
        self.tool_cache_misses_total: int = 0

        # Game server tick telemetry
        self.tick_events: Deque[Dict[str, Any]] = deque(maxlen=300)
        self.tick_overruns_total: int = 0
        self.stale_drops_total: int = 0
        self.agent_rtt_samples: Dict[str, Deque[float]] = {}

        # Compare latency with logging on vs off
        self.latency_when_logging_on: Deque[float] = deque(maxlen=150)
        self.latency_when_logging_off: Deque[float] = deque(maxlen=150)

    def record_span_dict(self, span_dict: Dict[str, Any]) -> None:
        sname = str(span_dict.get("name") or "").lower()
        is_priority = (
            any(
                k in sname
                for k in (
                    "genai.",
                    "coach.",
                    "actor.",
                    "mcp",
                    "a2a",
                    "eval",
                    "invocation",
                    "invoke_agent",
                    "call_llm",
                    "generate_content",
                    "execute_tool",
                    "tools/call",
                    "tools/list",
                    "initialize",
                )
            )
            or span_dict.get("status") == "ERROR"
        )
        with self._lock:
            self.spans.append(span_dict)
            if is_priority:
                self.priority_spans.append(span_dict)

    def record_log(
        self,
        level: str,
        message: str,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        tick: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        if not trace_id:
            span = trace.get_current_span()
            ctx = span.get_span_context() if span else None
            trace_id = f"{ctx.trace_id:032x}" if (ctx and ctx.is_valid) else None
            span_id = f"{ctx.span_id:016x}" if (ctx and ctx.is_valid) else None
        entry = {
            "timestamp": now.isoformat(),
            "time_str": now.strftime("%H:%M:%S.%f")[:-3],
            "service": self.service_name,
            "agent_id": self.agent_id,
            "level": level.upper(),
            "message": message,
            "trace_id": trace_id,
            "span_id": span_id,
            "tick": tick,
            "extra": extra or {},
        }
        with self._lock:
            self.logs.append(entry)

    def _current_or_recent_trace_span(self) -> tuple[Optional[str], Optional[str]]:
        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
            return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
        # Fallback if called immediately after span context exited
        with self._lock:
            if self.spans:
                last_sp = self.spans[-1]
                return last_sp.get("trace_id"), last_sp.get("span_id")
        return None, None

    def record_genai_call(
        self,
        role: str,
        model: str,
        latency_ms: float,
        ttft_ms: Optional[float] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        thinking_tokens: int = 0,
        thinking_budget: Optional[int] = None,
        prompt_chars: int = 0,
        status: str = "ok",
        decision_source: Optional[str] = None,
        tool_calls_count: int = 0,
        subturns_count: int = 1,
        tick: Optional[int] = None,
        error: Optional[str] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> None:
        now = time.time()
        if not trace_id:
            trace_id, span_id = self._current_or_recent_trace_span()
        est_input = input_tokens if input_tokens > 0 else max(1, prompt_chars // 4)
        total_tok = est_input + max(0, output_tokens) + max(0, thinking_tokens)
        effective_ttft = round(ttft_ms if ttft_ms is not None else latency_ms * 0.62, 2)

        evt = {
            "ts": now,
            "time_str": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
            "service": self.service_name,
            "agent_id": self.agent_id,
            "role": role,
            "model": model,
            "tick": tick,
            "latency_ms": round(float(latency_ms), 2),
            "ttft_ms": effective_ttft,
            "input_tokens": int(est_input),
            "output_tokens": int(output_tokens),
            "thinking_tokens": int(thinking_tokens),
            "total_tokens": int(total_tok),
            "thinking_budget": thinking_budget,
            "prompt_chars": int(prompt_chars),
            "status": status,
            "decision_source": decision_source or "adk_gemini",
            "tool_calls_count": int(tool_calls_count),
            "subturns_count": int(subturns_count),
            "error": error,
            "trace_id": trace_id,
            "span_id": span_id,
        }
        with self._lock:
            self.genai_events.append(evt)
            self.genai_totals["calls_total"] += 1
            if role == "actor":
                self.genai_totals["actor_calls"] += 1
            elif role == "coach":
                self.genai_totals["coach_calls"] += 1
            else:
                self.genai_totals["interrogator_calls"] += 1

            self.genai_totals["input_tokens_total"] += int(est_input)
            self.genai_totals["output_tokens_total"] += int(output_tokens)
            self.genai_totals["thinking_tokens_total"] += int(thinking_tokens)

            status_norm = str(status or "ok").lower()
            if status_norm == "429_rate_limit" or (error and ("429" in error or "RESOURCE_EXHAUSTED" in error)):
                self.genai_totals["errors_429_total"] += 1
            elif status_norm == "timeout" or (error and "timeout" in error.lower()):
                self.genai_totals["errors_timeout_total"] += 1
            elif status_norm not in ("ok", "success", "unset") or error:
                self.genai_totals["errors_other_total"] += 1

            src = decision_source or "adk_gemini"
            ds = self.genai_totals["decision_sources"]
            ds[src] = ds.get(src, 0) + 1

            if is_file_logging_enabled():
                self.latency_when_logging_on.append(float(latency_ms))
            else:
                self.latency_when_logging_off.append(float(latency_ms))

    def record_decision_source(self, source: str) -> None:
        with self._lock:
            ds = self.genai_totals["decision_sources"]
            ds[source] = ds.get(source, 0) + 1

    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
        status: str = "ok",
        cache_hit: bool = False,
        cached: Optional[bool] = None,
        payload_bytes: int = 0,
        agent_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> None:
        resolved_cache_hit = bool(cached) if cached is not None else bool(cache_hit)
        now = time.time()
        if not trace_id:
            trace_id, span_id = self._current_or_recent_trace_span()
        evt = {
            "ts": now,
            "time_str": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
            "service": self.service_name,
            "agent_id": agent_id or self.agent_id,
            "tool_name": tool_name,
            "duration_ms": round(float(duration_ms), 2),
            "status": status,
            "cache_hit": resolved_cache_hit,
            "payload_bytes": int(payload_bytes),
            "details": details or {},
            "trace_id": trace_id,
            "span_id": span_id,
        }
        with self._lock:
            self.tool_events.append(evt)
            if resolved_cache_hit:
                self.tool_cache_hits_total += 1
            else:
                self.tool_cache_misses_total += 1

            st = self.tool_stats.setdefault(
                tool_name,
                {
                    "calls": 0,
                    "errors": 0,
                    "cache_hits": 0,
                    "cache_misses": 0,
                    "latencies": deque(maxlen=100),
                },
            )
            st["calls"] += 1
            if str(status).lower() not in ("ok", "success"):
                st["errors"] += 1
            if resolved_cache_hit:
                st["cache_hits"] += 1
            else:
                st["cache_misses"] += 1
            st["latencies"].append(float(duration_ms))

    def record_sim_tick(
        self,
        tick: int,
        round_num: int,
        duration_ms: float,
        tick_budget_ms: Optional[float] = None,
        alive_cats: int = 0,
        alive_mice: int = 0,
        agent_rtts_ms: Optional[Dict[str, float]] = None,
        stale_count: int = 0,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> None:
        now = time.time()
        if not trace_id:
            trace_id, span_id = self._current_or_recent_trace_span()
        overrun = bool(tick_budget_ms and duration_ms > tick_budget_ms)
        evt = {
            "ts": now,
            "time_str": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
            "tick": tick,
            "round": round_num,
            "duration_ms": round(float(duration_ms), 2),
            "tick_budget_ms": round(float(tick_budget_ms), 2) if tick_budget_ms else None,
            "overrun": overrun,
            "alive_cats": alive_cats,
            "alive_mice": alive_mice,
            "stale_count": stale_count,
            "trace_id": trace_id,
            "span_id": span_id,
        }
        with self._lock:
            self.tick_events.append(evt)
            if overrun:
                self.tick_overruns_total += 1
            self.stale_drops_total += int(stale_count)
            for cid, rtt in (agent_rtts_ms or {}).items():
                q = self.agent_rtt_samples.setdefault(cid, deque(maxlen=100))
                q.append(float(rtt))

    def get_summary(self, include_spans: bool = True, span_limit: int = 250) -> Dict[str, Any]:
        now = time.time()
        cutoff_60s = now - 60.0
        with self._lock:
            genai_list = list(self.genai_events)
            actor_events = [e for e in genai_list if e["role"] == "actor"]
            coach_events = [e for e in genai_list if e["role"] == "coach"]
            recent_60s = [e for e in genai_list if e["ts"] >= cutoff_60s]

            actor_lats = [e["latency_ms"] for e in actor_events]
            actor_ttfts = [e["ttft_ms"] for e in actor_events if e.get("ttft_ms") is not None]
            coach_lats = [e["latency_ms"] for e in coach_events]
            coach_ttfts = [e["ttft_ms"] for e in coach_events if e.get("ttft_ms") is not None]
            coach_tools = [e.get("tool_calls_count", 0) for e in coach_events]

            tpm_60s = sum(e["total_tokens"] for e in recent_60s)
            input_tpm_60s = sum(e["input_tokens"] for e in recent_60s)
            output_tpm_60s = sum(e["output_tokens"] for e in recent_60s)
            thinking_tpm_60s = sum(e["thinking_tokens"] for e in recent_60s)
            rpm_60s = len(recent_60s)

            # Tool summary
            tools_out: Dict[str, Any] = {}
            for tname, st in self.tool_stats.items():
                tlats = list(st["latencies"])
                tools_out[tname] = {
                    "calls": st["calls"],
                    "errors": st["errors"],
                    "cache_hits": st["cache_hits"],
                    "cache_misses": st["cache_misses"],
                    "cache_hit_pct": round((st["cache_hits"] / st["calls"]) * 100.0, 1) if st["calls"] else 0.0,
                    "p50_ms": _percentile(tlats, 50),
                    "p95_ms": _percentile(tlats, 95),
                    "last_ms": round(tlats[-1], 2) if tlats else 0.0,
                }

            total_cache_able = self.tool_cache_hits_total + self.tool_cache_misses_total
            cache_hit_pct = (
                round((self.tool_cache_hits_total / total_cache_able) * 100.0, 1)
                if total_cache_able > 0
                else 0.0
            )

            # Tick summary
            ticks_list = list(self.tick_events)
            tick_lats = [t["duration_ms"] for t in ticks_list]
            agent_rtts_out = {
                cid: {
                    "p50_ms": _percentile(list(q), 50),
                    "p95_ms": _percentile(list(q), 95),
                    "last_ms": round(q[-1], 2) if q else 0.0,
                }
                for cid, q in self.agent_rtt_samples.items()
            }

            log_on_lats = list(self.latency_when_logging_on)
            log_off_lats = list(self.latency_when_logging_off)

            actor_ttft_p50 = _percentile(actor_ttfts, 50)
            actor_ttft_p95 = _percentile(actor_ttfts, 95)
            actor_ttft_p99 = _percentile(actor_ttfts, 99)
            coach_ttft_p50 = _percentile(coach_ttfts, 50)
            coach_ttft_p95 = _percentile(coach_ttfts, 95)
            coach_ttft_p99 = _percentile(coach_ttfts, 99)

            res: Dict[str, Any] = {
                "service_name": self.service_name,
                "agent_id": self.agent_id,
                "uptime_sec": round(now - self.start_time, 1),
                "config": get_otel_config(),
                "genai": {
                    **self.genai_totals,
                    "decision_sources": dict(self.genai_totals["decision_sources"]),
                    "tpm_60s": tpm_60s,
                    "input_tpm_60s": input_tpm_60s,
                    "output_tpm_60s": output_tpm_60s,
                    "thinking_tpm_60s": thinking_tpm_60s,
                    "rpm_60s": rpm_60s,
                    "actor": {
                        "count": len(actor_events),
                        "calls": len(actor_events),
                        "latency_p50_ms": _percentile(actor_lats, 50),
                        "latency_p95_ms": _percentile(actor_lats, 95),
                        "latency_p99_ms": _percentile(actor_lats, 99),
                        "latency_last_ms": round(actor_lats[-1], 2) if actor_lats else 0.0,
                        "ttft_p50_ms": actor_ttft_p50,
                        "ttft_p95_ms": actor_ttft_p95,
                        "ttft_last_ms": round(actor_ttfts[-1], 2) if actor_ttfts else 0.0,
                        "ttft_ms": {
                            "p50": actor_ttft_p50,
                            "p95": actor_ttft_p95,
                            "p99": actor_ttft_p99,
                        },
                    },
                    "coach": {
                        "count": len(coach_events),
                        "calls": len(coach_events),
                        "latency_p50_ms": _percentile(coach_lats, 50),
                        "latency_p95_ms": _percentile(coach_lats, 95),
                        "latency_p99_ms": _percentile(coach_lats, 99),
                        "latency_last_ms": round(coach_lats[-1], 2) if coach_lats else 0.0,
                        "ttft_p50_ms": coach_ttft_p50,
                        "ttft_p95_ms": coach_ttft_p95,
                        "ttft_last_ms": round(coach_ttfts[-1], 2) if coach_ttfts else 0.0,
                        "ttft_ms": {
                            "p50": coach_ttft_p50,
                            "p95": coach_ttft_p95,
                            "p99": coach_ttft_p99,
                        },
                        "avg_tools_per_eval": round(statistics.mean(coach_tools), 2) if coach_tools else 0.0,
                    },
                    "logging_latency_impact": {
                        "logging_on_p50_ms": _percentile(log_on_lats, 50),
                        "logging_on_samples": len(log_on_lats),
                        "logging_off_p50_ms": _percentile(log_off_lats, 50),
                        "logging_off_samples": len(log_off_lats),
                    },
                    "recent_events": genai_list[-90:],
                },
                "tokens": {
                    "input_tokens_total": self.genai_totals["input_tokens_total"],
                    "output_tokens_total": self.genai_totals["output_tokens_total"],
                    "thinking_tokens_total": self.genai_totals["thinking_tokens_total"],
                    "rolling_60s_tpm": tpm_60s,
                    "rolling_60s_rpm": rpm_60s,
                },
                "tools": {
                    **tools_out,
                    "by_name": tools_out,
                    "cache_hits_total": self.tool_cache_hits_total,
                    "cache_misses_total": self.tool_cache_misses_total,
                    "cache_hit_pct": cache_hit_pct,
                    "recent_calls": list(self.tool_events)[-80:],
                },
                "simulation": {
                    "ticks_recorded": len(ticks_list),
                    "tick_p50_ms": _percentile(tick_lats, 50),
                    "tick_p95_ms": _percentile(tick_lats, 95),
                    "tick_p99_ms": _percentile(tick_lats, 99),
                    "tick_last_ms": round(tick_lats[-1], 2) if tick_lats else 0.0,
                    "tick_overruns_total": self.tick_overruns_total,
                    "stale_drops_total": self.stale_drops_total,
                    "agent_rtts": agent_rtts_out,
                    "recent_ticks": ticks_list[-90:],
                },
                "logs": list(self.logs)[-60:],
            }
            if include_spans:
                recent_tail = list(self.spans)[-span_limit:]
                prio_tail = list(self.priority_spans)[-span_limit:]
                prio_trace_ids = {sp.get("trace_id") for sp in prio_tail if sp.get("trace_id")}
                related_spans = [
                    sp for sp in self.spans
                    if sp.get("trace_id") in prio_trace_ids
                ]
                merged_by_id: Dict[str, Dict[str, Any]] = {}
                for sp in prio_tail + related_spans + recent_tail:
                    sid = sp.get("span_id")
                    if sid:
                        merged_by_id[sid] = sp
                merged_spans = sorted(
                    merged_by_id.values(),
                    key=lambda x: x.get("start_time_ns") or 0,
                )
                res["spans"] = merged_spans[-(span_limit * 2):]
            return res


# ---------------------------------------------------------------------------
# OpenTelemetry Span Exporters (InMemory SRE + Toggleable JSONL + OTLP HTTP)
# ---------------------------------------------------------------------------


def _sanitize_attr_val(val: Any, max_len: int = 4000) -> Any:
    if isinstance(val, str) and len(val) > max_len:
        return val[:max_len] + f"... [truncated, total {len(val)} chars]"
    return val


def _readable_span_to_dict(span: ReadableSpan, service_name: str, agent_id: str) -> Dict[str, Any]:
    ctx = span.get_span_context()
    parent = span.parent
    start_ns = span.start_time or 0
    end_ns = span.end_time or start_ns
    duration_ms = round(max(0.0, (end_ns - start_ns) / 1_000_000.0), 2)
    attrs = dict(span.attributes or {})
    status_code = span.status.status_code.name if span.status else "UNSET"
    return {
        "trace_id": f"{ctx.trace_id:032x}" if ctx else "0" * 32,
        "span_id": f"{ctx.span_id:016x}" if ctx else "0" * 16,
        "parent_span_id": f"{parent.span_id:016x}" if parent else None,
        "name": span.name,
        "kind": span.kind.name if span.kind else "INTERNAL",
        "service_name": service_name,
        "agent_id": attrs.get("cm.cat_id") or agent_id or "",
        "tick": attrs.get("cm.tick"),
        "start_time_ns": start_ns,
        "end_time_ns": end_ns,
        "timestamp": datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).isoformat() if start_ns else "",
        "time_str": datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3] if start_ns else "",
        "duration_ms": duration_ms,
        "status": status_code,
        "status_description": span.status.description if span.status else None,
        "attributes": {str(k): _sanitize_attr_val(v) for k, v in attrs.items()},
    }


class InMemoryAndJSONLSpanExporter(SpanExporter):
    """Exports OTel spans into the local SRETelemetryStore and optionally to disk JSONL / OTLP."""

    def __init__(self, store: SRETelemetryStore, jsonl_path: Optional[Path] = None) -> None:
        self.store = store
        self.jsonl_path = jsonl_path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not is_tracing_enabled():
            return SpanExportResult.SUCCESS
        write_jsonl = is_file_logging_enabled() and self.jsonl_path is not None
        lines_to_write: List[str] = []

        for sp in spans:
            d = _readable_span_to_dict(sp, self.store.service_name, self.store.agent_id)
            self.store.record_span_dict(d)
            if write_jsonl:
                try:
                    lines_to_write.append(json.dumps(d, default=str))
                except Exception:
                    pass

        if write_jsonl and lines_to_write and self.jsonl_path is not None:
            try:
                self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.jsonl_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines_to_write) + "\n")
            except Exception:
                pass

        # Optional OTLP HTTP JSON forwarder or Google Cloud Trace v2 batchWrite
        cfg = get_otel_config()
        span_dicts = [_readable_span_to_dict(s, self.store.service_name, self.store.agent_id) for s in spans]
        if cfg.get("backend") == "otlp" and cfg.get("otlp_endpoint"):
            self._forward_otlp_nonblocking(span_dicts, cfg["otlp_endpoint"])
        elif cfg.get("backend") == "gcp":
            self._forward_gcp_cloud_trace_nonblocking(span_dicts, cfg.get("gcp_project") or "")

        return SpanExportResult.SUCCESS

    def _forward_otlp_nonblocking(self, span_dicts: List[Dict[str, Any]], endpoint: str) -> None:
        try:
            payload = {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {"key": "service.name", "value": {"stringValue": self.store.service_name}},
                            ]
                        },
                        "scopeSpans": [{"spans": span_dicts}],
                    }
                ]
            }
            data = json.dumps(payload, default=str).encode("utf-8")
            url = endpoint.rstrip("/")
            if not url.endswith("/v1/traces"):
                url = f"{url}/v1/traces"
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=0.75).close()
        except Exception:
            pass

    def _forward_gcp_cloud_trace_nonblocking(self, span_dicts: List[Dict[str, Any]], configured_project: str) -> None:
        def _send() -> None:
            try:
                import google.auth
                import google.auth.transport.requests

                creds, detected_project = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/trace.append", "https://www.googleapis.com/auth/cloud-platform"]
                )
                project_id = configured_project or detected_project or "agent-zoo"
                if not creds.valid:
                    creds.refresh(google.auth.transport.requests.Request())
                gcp_spans = []
                for sd in span_dicts:
                    tid = sd["trace_id"]
                    sid = sd["span_id"]
                    attr_map = {
                        "service.name": {"stringValue": {"value": str(sd.get("service_name") or "")[:256]}},
                        "cm.agent_id": {"stringValue": {"value": str(sd.get("agent_id") or "")[:256]}},
                    }
                    for ak, av in (sd.get("attributes") or {}).items():
                        attr_map[str(ak)[:128]] = {"stringValue": {"value": str(av)[:512]}}
                    span_obj: Dict[str, Any] = {
                        "name": f"projects/{project_id}/traces/{tid}/spans/{sid}",
                        "spanId": sid,
                        "displayName": {"value": str(sd.get("name") or "span")[:128]},
                        "startTime": sd.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                        "endTime": datetime.fromtimestamp((sd.get("end_time_ns") or time.time_ns()) / 1e9, tz=timezone.utc).isoformat(),
                        "attributes": {"attributeMap": attr_map},
                    }
                    if sd.get("parent_span_id"):
                        span_obj["parentSpanId"] = sd["parent_span_id"]
                    gcp_spans.append(span_obj)
                url = f"https://cloudtrace.googleapis.com/v2/projects/{project_id}/traces:batchWrite"
                body = json.dumps({"spans": gcp_spans}).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {creds.token}",
                        "x-goog-user-project": project_id,
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=2.5).close()
            except Exception:
                pass

        threading.Thread(target=_send, daemon=True).start()

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Singleton Initialization & Prometheus Text Format Renderer
# ---------------------------------------------------------------------------

_GLOBAL_SRE_STORE: Optional[SRETelemetryStore] = None
_INITIALIZED_SERVICES: Dict[str, SRETelemetryStore] = {}
_INIT_LOCK = threading.Lock()


def get_sre_store() -> SRETelemetryStore:
    global _GLOBAL_SRE_STORE
    if _GLOBAL_SRE_STORE is None:
        _GLOBAL_SRE_STORE = init_otel(
            service_name=os.environ.get("OTEL_SERVICE_NAME") or "cm-service",
            agent_id=os.environ.get("CAT_ID") or "",
        )
    return _GLOBAL_SRE_STORE


def init_otel(
    service_name: str,
    agent_id: str = "",
    port: Optional[int] = None,
    log_dir: Optional[Path] = None,
) -> SRETelemetryStore:
    """Initializes OpenTelemetry TracerProvider, MeterProvider, and SRETelemetryStore for a server process."""
    global _GLOBAL_SRE_STORE
    with _INIT_LOCK:
        key = f"{service_name}:{agent_id}"
        if key in _INITIALIZED_SERVICES:
            _GLOBAL_SRE_STORE = _INITIALIZED_SERVICES[key]
            return _GLOBAL_SRE_STORE

        store = SRETelemetryStore(service_name=service_name, agent_id=agent_id)
        root_dir = Path(__file__).resolve().parents[2]
        target_log_dir = log_dir or (root_dir / "logs")
        safe_name = f"{service_name}_{agent_id}".strip("_").replace(":", "_")
        jsonl_path = target_log_dir / f"otel_traces_{safe_name}.jsonl"

        resource_attrs: Dict[str, Any] = {
            "service.name": service_name,
            "cm.agent.id": agent_id or "none",
            "cm.backend": get_otel_config()["backend"],
        }
        if port is not None:
            resource_attrs["service.instance.port"] = int(port)

        resource = Resource.create(resource_attrs)
        provider = TracerProvider(resource=resource)
        exporter = InMemoryAndJSONLSpanExporter(store=store, jsonl_path=jsonl_path)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        try:
            trace.set_tracer_provider(provider)
        except Exception:
            pass

        try:
            meter_provider = MeterProvider(resource=resource)
            metrics.set_meter_provider(meter_provider)
        except Exception:
            pass

        # Attach log correlation filter
        log_filter = OTelLogCorrelationFilter(service_name=service_name, agent_id=agent_id)
        logging.getLogger().addFilter(log_filter)

        _INITIALIZED_SERVICES[key] = store
        _GLOBAL_SRE_STORE = store
        return store


def render_prometheus_metrics(store: Optional[SRETelemetryStore] = None) -> str:
    """Renders OpenTelemetry / SRE metrics in standard Prometheus text exposition format (0.0.4)."""
    st = store or get_sre_store()
    summary = st.get_summary(include_spans=False)
    svc = summary["service_name"]
    aid = summary["agent_id"] or "none"
    labels = f'service="{svc}",agent_id="{aid}"'

    g = summary["genai"]
    sim = summary["simulation"]
    tools = summary["tools"]

    lines = [
        "# HELP cm_uptime_seconds Process uptime in seconds",
        "# TYPE cm_uptime_seconds gauge",
        f"cm_uptime_seconds{{{labels}}} {summary['uptime_sec']}",
        "# HELP cm_genai_calls_total Total GenAI LLM invocations",
        "# TYPE cm_genai_calls_total counter",
        f'cm_genai_calls_total{{{labels},role="actor"}} {g["actor_calls"]}',
        f'cm_genai_calls_total{{{labels},role="coach"}} {g["coach_calls"]}',
        "# HELP cm_genai_tokens_total Total GenAI tokens consumed by type",
        "# TYPE cm_genai_tokens_total counter",
        f'cm_genai_tokens_total{{{labels},type="input"}} {g["input_tokens_total"]}',
        f'cm_genai_tokens_total{{{labels},type="output"}} {g["output_tokens_total"]}',
        f'cm_genai_tokens_total{{{labels},type="thinking"}} {g["thinking_tokens_total"]}',
        "# HELP cm_genai_tpm_60s Rolling 60-second tokens per minute burn rate",
        "# TYPE cm_genai_tpm_60s gauge",
        f"cm_genai_tpm_60s{{{labels}}} {g['tpm_60s']}",
        f"cm_genai_tpm_rolling_60s{{{labels}}} {g['tpm_60s']}",
        "# HELP cm_genai_rpm_60s Rolling 60-second requests per minute rate",
        "# TYPE cm_genai_rpm_60s gauge",
        f"cm_genai_rpm_60s{{{labels}}} {g['rpm_60s']}",
        "# HELP cm_genai_errors_total Total GenAI errors by category",
        "# TYPE cm_genai_errors_total counter",
        f'cm_genai_errors_total{{{labels},error_type="429_rate_limit"}} {g["errors_429_total"]}',
        f'cm_genai_errors_total{{{labels},error_type="timeout"}} {g["errors_timeout_total"]}',
        f'cm_genai_errors_total{{{labels},error_type="other"}} {g["errors_other_total"]}',
        "# HELP cm_genai_latency_ms GenAI turn latency percentiles in milliseconds",
        "# TYPE cm_genai_latency_ms gauge",
        f'cm_genai_latency_ms{{{labels},role="actor",quantile="0.5"}} {g["actor"]["latency_p50_ms"]}',
        f'cm_genai_latency_ms{{{labels},role="actor",quantile="0.95"}} {g["actor"]["latency_p95_ms"]}',
        f'cm_genai_latency_ms{{{labels},role="actor",quantile="0.99"}} {g["actor"]["latency_p99_ms"]}',
        f'cm_genai_latency_ms{{{labels},role="coach",quantile="0.5"}} {g["coach"]["latency_p50_ms"]}',
        f'cm_genai_latency_ms{{{labels},role="coach",quantile="0.95"}} {g["coach"]["latency_p95_ms"]}',
        "# HELP cm_genai_ttft_ms GenAI Time To First Token (TTFT) percentiles in milliseconds",
        "# TYPE cm_genai_ttft_ms gauge",
        f'cm_genai_ttft_ms{{{labels},role="actor",quantile="0.5"}} {g["actor"]["ttft_p50_ms"]}',
        f'cm_genai_ttft_ms{{{labels},role="actor",quantile="0.95"}} {g["actor"]["ttft_p95_ms"]}',
        f'cm_genai_ttft_ms{{{labels},role="coach",quantile="0.5"}} {g["coach"]["ttft_p50_ms"]}',
        f'cm_genai_ttft_ms{{{labels},role="coach",quantile="0.95"}} {g["coach"]["ttft_p95_ms"]}',
        "# HELP cm_tool_cache_hits_total Total tool response cache hits and misses",
        "# TYPE cm_tool_cache_hits_total counter",
        f'cm_tool_cache_hits_total{{{labels},result="hit"}} {tools["cache_hits_total"]}',
        f'cm_tool_cache_hits_total{{{labels},result="miss"}} {tools["cache_misses_total"]}',
        "# HELP cm_sim_tick_duration_ms Simulation tick duration percentiles in milliseconds",
        "# TYPE cm_sim_tick_duration_ms gauge",
        f'cm_sim_tick_duration_ms{{{labels},quantile="0.5"}} {sim["tick_p50_ms"]}',
        f'cm_sim_tick_duration_ms{{{labels},quantile="0.95"}} {sim["tick_p95_ms"]}',
        f'cm_sim_tick_duration_ms{{{labels},quantile="0.99"}} {sim["tick_p99_ms"]}',
        "# HELP cm_sim_tick_overruns_total Total simulation ticks that exceeded tick budget",
        "# TYPE cm_sim_tick_overruns_total counter",
        f"cm_sim_tick_overruns_total{{{labels}}} {sim['tick_overruns_total']}",
        f'cm_tool_calls_total{{{labels},tool="_all"}} {sum(t["calls"] for t in tools["by_name"].values())}',
    ]

    for src_name, cnt in g["decision_sources"].items():
        lines.append(f'cm_agent_decisions_total{{{labels},source="{src_name}"}} {cnt}')

    for tool_name, tst in tools["by_name"].items():
        lines.append(f'cm_tool_calls_total{{{labels},tool="{tool_name}"}} {tst["calls"]}')
        lines.append(f'cm_tool_errors_total{{{labels},tool="{tool_name}"}} {tst["errors"]}')
        lines.append(f'cm_tool_latency_ms{{{labels},tool="{tool_name}",quantile="0.5"}} {tst["p50_ms"]}')
        lines.append(f'cm_tool_latency_ms{{{labels},tool="{tool_name}",quantile="0.95"}} {tst["p95_ms"]}')

    return "\n".join(lines) + "\n"
