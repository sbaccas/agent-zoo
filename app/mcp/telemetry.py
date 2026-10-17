"""Real-time telemetry and event streaming tracker for Cat & Mouse MCP Server."""

import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("cat_mcp.telemetry")


class MCPTelemetryTracker:
    """In-memory event ring buffer and real-time SSE broadcaster for MCP activity."""

    def __init__(self, max_events: int = 300):
        self.max_events = max_events
        self._events: deque = deque(maxlen=max_events)
        self._subscribers: Set[asyncio.Queue] = set()
        self._counter: int = 0
        self.start_time: float = time.time()
        self.active_clients: int = 0
        self.total_requests: int = 0
        self.total_tool_calls: int = 0
        self.total_resource_reads: int = 0
        self.total_errors: int = 0
        self.tool_counts: Dict[str, int] = {}
        self.latencies: deque = deque(maxlen=50)

    def record_event(
        self,
        event_type: str,
        level: str = "info",
        summary: str = "",
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        tool: Optional[str] = None,
        resource: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        status: str = "ok",
        details: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Record an event, update metrics, and broadcast to active SSE subscribers."""
        self._counter += 1
        now = datetime.now(timezone.utc)

        # Update stats
        if event_type == "tool_call":
            self.total_tool_calls += 1
            if tool:
                self.tool_counts[tool] = self.tool_counts.get(tool, 0) + 1
            if duration_ms is not None:
                self.latencies.append(duration_ms)
            try:
                from app.otel.setup import get_sre_store
                get_sre_store().record_tool_call(
                    tool_name=f"mcp.{tool or 'unknown'}",
                    duration_ms=float(duration_ms or 0.0),
                    status="ERROR" if (level == "error" or status == "error") else "OK",
                    cached=False,
                )
            except Exception:
                pass
        elif event_type == "resource_read":
            self.total_resource_reads += 1
        elif event_type == "request":
            self.total_requests += 1

        if level == "error" or status == "error":
            self.total_errors += 1

        event_payload = {
            "id": f"evt-{self._counter}-{int(time.time() * 1000) % 100000}",
            "timestamp": now.isoformat(),
            "time_str": now.strftime("%H:%M:%S.%f")[:-3],
            "type": event_type,
            "level": level,
            "summary": summary,
            "client_ip": client_ip or "127.0.0.1",
            "user_agent": user_agent or "",
            "tool": tool,
            "resource": resource,
            "arguments": arguments,
            "duration_ms": duration_ms,
            "status": status,
            "details": details,
        }

        self._events.append(event_payload)

        # Broadcast to all live SSE client queues
        dead_queues = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(event_payload)
            except Exception:
                dead_queues.append(q)

        for dq in dead_queues:
            self._subscribers.discard(dq)

        return event_payload

    def incr_active_clients(self):
        self.active_clients += 1

    def decr_active_clients(self):
        self.active_clients = max(0, self.active_clients - 1)

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE stream listener."""
        q = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Remove an SSE stream listener."""
        self._subscribers.discard(q)

    def get_history(self, limit: int = 100, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return chronological list of recent events."""
        events = list(self._events)
        if event_type:
            events = [e for e in events if e.get("type") == event_type]
        return events[-limit:]

    def clear_history(self):
        """Clear all stored events."""
        self._events.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Return server metrics."""
        uptime_sec = int(time.time() - self.start_time)
        avg_latency = round(sum(self.latencies) / len(self.latencies), 1) if self.latencies else 0.0
        return {
            "uptime_seconds": uptime_sec,
            "active_clients": self.active_clients,
            "total_requests": self.total_requests,
            "total_tool_calls": self.total_tool_calls,
            "total_resource_reads": self.total_resource_reads,
            "total_errors": self.total_errors,
            "tool_counts": self.tool_counts,
            "avg_latency_ms": avg_latency,
            "total_events_stored": len(self._events),
        }


# Singleton tracker
_GLOBAL_TRACKER: Optional[MCPTelemetryTracker] = None


def get_telemetry() -> MCPTelemetryTracker:
    global _GLOBAL_TRACKER
    if _GLOBAL_TRACKER is None:
        _GLOBAL_TRACKER = MCPTelemetryTracker()
    return _GLOBAL_TRACKER


def reset_telemetry():
    global _GLOBAL_TRACKER
    _GLOBAL_TRACKER = MCPTelemetryTracker()
