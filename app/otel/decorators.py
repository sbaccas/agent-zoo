"""Ergonomic OpenTelemetry decorators and W3C traceparent propagation helpers."""

import functools
import inspect
import time
from typing import Any, Callable, Dict, Optional

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.otel.setup import get_sre_store, is_tracing_enabled

_PROPAGATOR = TraceContextTextMapPropagator()


def inject_traceparent(carrier: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Injects the current active OTel span's W3C `traceparent` into a dictionary carrier and returns the traceparent string."""
    target = carrier if carrier is not None else {}
    if not is_tracing_enabled():
        return target.get("traceparent")
    try:
        _PROPAGATOR.inject(target)
    except Exception:
        pass
    return target.get("traceparent")


def extract_traceparent(carrier: Optional[Dict[str, Any]] = None):
    """Extracts an OpenTelemetry Context from a dictionary containing `traceparent`."""
    if not carrier or not is_tracing_enabled():
        return None
    try:
        if "traceparent" in carrier:
            return _PROPAGATOR.extract({"traceparent": str(carrier["traceparent"])})
    except Exception:
        pass
    return None


def traced_span(
    name: Optional[str] = None,
    attributes: Optional[Any] = None,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes_fn: Optional[Callable[..., Dict[str, Any]]] = None,
):
    """Decorator that wraps a sync or async function in an OpenTelemetry span when tracing is enabled."""
    actual_kind = attributes if isinstance(attributes, SpanKind) else kind
    static_attrs: Dict[str, Any] = dict(attributes) if isinstance(attributes, dict) else {}
    attr_callable = attributes if callable(attributes) else attributes_fn

    def decorator(func: Callable):
        span_name = name or func.__qualname__

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if not is_tracing_enabled():
                    return await func(*args, **kwargs)
                tracer = trace.get_tracer("cm.tracer")
                attrs: Dict[str, Any] = dict(static_attrs)
                if attr_callable is not None:
                    try:
                        attrs.update(attr_callable(*args, **kwargs) or {})
                    except Exception:
                        pass
                with tracer.start_as_current_span(span_name, kind=actual_kind, attributes=attrs) as span:
                    try:
                        result = await func(*args, **kwargs)
                        span.set_status(Status(StatusCode.OK))
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        get_sre_store().record_log(
                            "ERROR",
                            f"Exception in {span_name}: {type(exc).__name__}: {exc}",
                        )
                        raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not is_tracing_enabled():
                return func(*args, **kwargs)
            tracer = trace.get_tracer("cm.tracer")
            attrs: Dict[str, Any] = dict(static_attrs)
            if attr_callable is not None:
                try:
                    attrs.update(attr_callable(*args, **kwargs) or {})
                except Exception:
                    pass
            with tracer.start_as_current_span(span_name, kind=actual_kind, attributes=attrs) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    get_sre_store().record_log(
                        "ERROR",
                        f"Exception in {span_name}: {type(exc).__name__}: {exc}",
                    )
                    raise

        return sync_wrapper

    return decorator


def traced_genai(
    role: str = "actor",
    model: Optional[str] = None,
    span_name: Optional[str] = None,
):
    """Decorator for GenAI / LLM deliberation and Coach evaluation functions."""

    def decorator(func: Callable):
        sname = span_name or f"genai.{role}_deliberate"
        base_attrs: Dict[str, Any] = {"gen_ai.role": role, "gen_ai.system": "google_genai"}
        if model:
            base_attrs["gen_ai.request.model"] = model

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if not is_tracing_enabled():
                    return await func(*args, **kwargs)
                tracer = trace.get_tracer("cm.genai")
                with tracer.start_as_current_span(
                    sname,
                    kind=SpanKind.CLIENT,
                    attributes=dict(base_attrs),
                ) as span:
                    try:
                        res = await func(*args, **kwargs)
                        span.set_status(Status(StatusCode.OK))
                        return res
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not is_tracing_enabled():
                return func(*args, **kwargs)
            tracer = trace.get_tracer("cm.genai")
            with tracer.start_as_current_span(
                sname,
                kind=SpanKind.CLIENT,
                attributes=dict(base_attrs),
            ) as span:
                try:
                    res = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return res
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise

        return sync_wrapper

    return decorator


def traced_tool(tool_name: Optional[str] = None):
    """Decorator for Coach / MCP tool functions that records OTel spans and SRE tool metrics."""

    def decorator(func: Callable):
        tname = tool_name or func.__name__

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                if not is_tracing_enabled():
                    res = await func(*args, **kwargs)
                    dur_ms = (time.perf_counter() - t0) * 1000.0
                    get_sre_store().record_tool_call(tname, dur_ms, status="ok")
                    return res

                tracer = trace.get_tracer("cm.tools")
                with tracer.start_as_current_span(
                    f"mcp.tool.{tname}",
                    kind=SpanKind.CLIENT,
                    attributes={"cm.tool.name": tname},
                ) as span:
                    try:
                        res = await func(*args, **kwargs)
                        dur_ms = (time.perf_counter() - t0) * 1000.0
                        cache_hit = bool(isinstance(res, dict) and res.get("_cache_hit"))
                        ok = True
                        if isinstance(res, dict) and "ok" in res:
                            ok = bool(res.get("ok"))
                        status_str = "ok" if ok else "error"
                        span.set_attribute("cm.tool.duration_ms", round(dur_ms, 2))
                        span.set_attribute("cm.tool.cache_hit", cache_hit)
                        span.set_attribute("cm.tool.status", status_str)
                        span.set_status(Status(StatusCode.OK if ok else StatusCode.ERROR))
                        get_sre_store().record_tool_call(
                            tname,
                            dur_ms,
                            status=status_str,
                            cache_hit=cache_hit,
                        )
                        return res
                    except Exception as exc:
                        dur_ms = (time.perf_counter() - t0) * 1000.0
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        get_sre_store().record_tool_call(tname, dur_ms, status="error")
                        raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            if not is_tracing_enabled():
                res = func(*args, **kwargs)
                dur_ms = (time.perf_counter() - t0) * 1000.0
                get_sre_store().record_tool_call(tname, dur_ms, status="ok")
                return res

            tracer = trace.get_tracer("cm.tools")
            with tracer.start_as_current_span(
                f"mcp.tool.{tname}",
                kind=SpanKind.CLIENT,
                attributes={"cm.tool.name": tname},
            ) as span:
                try:
                    res = func(*args, **kwargs)
                    dur_ms = (time.perf_counter() - t0) * 1000.0
                    cache_hit = bool(isinstance(res, dict) and res.get("_cache_hit"))
                    ok = True
                    if isinstance(res, dict) and "ok" in res:
                        ok = bool(res.get("ok"))
                    status_str = "ok" if ok else "error"
                    span.set_attribute("cm.tool.duration_ms", round(dur_ms, 2))
                    span.set_attribute("cm.tool.cache_hit", cache_hit)
                    span.set_attribute("cm.tool.status", status_str)
                    span.set_status(Status(StatusCode.OK if ok else StatusCode.ERROR))
                    get_sre_store().record_tool_call(
                        tname,
                        dur_ms,
                        status=status_str,
                        cache_hit=cache_hit,
                    )
                    return res
                except Exception as exc:
                    dur_ms = (time.perf_counter() - t0) * 1000.0
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    get_sre_store().record_tool_call(tname, dur_ms, status="error")
                    raise

        return sync_wrapper

    return decorator


def record_genai_metrics(
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
    error_type: Optional[str] = None,
) -> None:
    """Records GenAI call telemetry on both the active OTel span and the SRETelemetryStore."""
    resolved_error = error if error is not None else error_type
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("gen_ai.request.model", model)
        if thinking_budget is not None:
            span.set_attribute("gen_ai.request.thinking_budget", int(thinking_budget))
        span.set_attribute("gen_ai.response.total_latency_ms", round(float(latency_ms), 2))
        if ttft_ms is not None:
            span.set_attribute("gen_ai.response.ttft_ms", round(float(ttft_ms), 2))
        span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens or max(1, prompt_chars // 4)))
        span.set_attribute("gen_ai.usage.output_tokens", int(output_tokens))
        span.set_attribute("gen_ai.usage.thinking_tokens", int(thinking_tokens))
        span.set_attribute("cm.prompt_chars", int(prompt_chars))
        span.set_attribute("cm.status", status)
        span.set_attribute("cm.coach.tool_calls_count", int(tool_calls_count))
        if tick is not None:
            span.set_attribute("cm.tick", int(tick))
        if resolved_error:
            span.set_attribute("cm.error_type", str(resolved_error))

    get_sre_store().record_genai_call(
        role=role,
        model=model,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        thinking_budget=thinking_budget,
        prompt_chars=prompt_chars,
        status=status,
        decision_source=decision_source,
        tool_calls_count=tool_calls_count,
        subturns_count=subturns_count,
        tick=tick,
        error=resolved_error,
    )


def record_tool_metrics(
    tool_name: str,
    duration_ms: float,
    status: str = "ok",
    cache_hit: bool = False,
    cached: Optional[bool] = None,
    payload_bytes: int = 0,
    agent_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Records tool execution metrics on the current span and SRE store."""
    resolved_cache_hit = bool(cached) if cached is not None else bool(cache_hit)
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("cm.tool.name", tool_name)
        span.set_attribute("cm.tool.duration_ms", round(float(duration_ms), 2))
        span.set_attribute("cm.tool.cache_hit", resolved_cache_hit)
        span.set_attribute("cm.tool.status", status)
    get_sre_store().record_tool_call(
        tool_name=tool_name,
        duration_ms=duration_ms,
        status=status,
        cache_hit=resolved_cache_hit,
        payload_bytes=payload_bytes,
        agent_id=agent_id,
        details=details,
    )


def record_sim_tick_metrics(
    tick_or_duration: float = 0.0,
    round_num: int = 0,
    duration_ms: Optional[float] = None,
    tick_budget_ms: Optional[float] = None,
    alive_cats: int = 0,
    alive_mice: int = 0,
    agent_rtts_ms: Optional[Dict[str, float]] = None,
    stale_count: int = 0,
    tick: Optional[int] = None,
) -> None:
    """Records Game Server tick execution metrics on the current span and SRE store."""
    if duration_ms is None:
        resolved_duration_ms = float(tick_or_duration)
        resolved_tick = int(tick or 0)
    else:
        resolved_duration_ms = float(duration_ms)
        resolved_tick = int(tick if tick is not None else tick_or_duration)

    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("cm.tick", int(resolved_tick))
        span.set_attribute("cm.round", int(round_num))
        span.set_attribute("cm.tick_duration_ms", round(float(resolved_duration_ms), 2))
        if tick_budget_ms is not None:
            span.set_attribute("cm.tick_budget_ms", round(float(tick_budget_ms), 2))
            span.set_attribute("cm.tick_overrun", bool(resolved_duration_ms > tick_budget_ms))
        span.set_attribute("cm.alive_cats", int(alive_cats))
        span.set_attribute("cm.alive_mice", int(alive_mice))
        span.set_attribute("cm.stale_count", int(stale_count))

    get_sre_store().record_sim_tick(
        tick=resolved_tick,
        round_num=round_num,
        duration_ms=resolved_duration_ms,
        tick_budget_ms=tick_budget_ms,
        alive_cats=alive_cats,
        alive_mice=alive_mice,
        agent_rtts_ms=agent_rtts_ms,
        stale_count=stale_count,
    )

