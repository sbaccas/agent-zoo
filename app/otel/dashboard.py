"""Unified SRE Dashboard HTML & cluster aggregation helpers for Cat & Mouse."""

import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from app.otel.setup import get_otel_config, get_sre_store, update_otel_config


def discover_cluster_nodes() -> List[Dict[str, str]]:
    """Returns all known nodes in the Cat & Mouse cluster (Game Server, MCP Server, Cat Agents)."""
    game_url = (os.environ.get("GAME_SERVER_URL") or "http://127.0.0.1:8000").rstrip("/")
    mcp_port = os.environ.get("CAT_MCP_PORT") or "9050"
    mcp_url = (os.environ.get("CAT_MCP_SERVER_URL") or f"http://127.0.0.1:{mcp_port}").rstrip("/")

    nodes: List[Dict[str, str]] = [
        {"id": "game-server", "label": "Game Server (:8000)", "role": "game_server", "url": game_url},
        {"id": "mcp-server", "label": f"MCP Server (:{mcp_port})", "role": "mcp_server", "url": mcp_url},
    ]

    peer_raw = os.environ.get("CAT_PEER_REGISTRY")
    peers: Dict[str, str] = {}
    if peer_raw:
        try:
            parsed = json.loads(peer_raw)
            if isinstance(parsed, dict):
                peers = {str(k): str(v).rstrip("/") for k, v in parsed.items()}
        except Exception:
            pass

    if not peers:
        peers = {
            "cat-1": "http://127.0.0.1:8001",
            "cat-2": "http://127.0.0.1:8003",
            "cat-3": "http://127.0.0.1:8005",
        }

    seen_urls = set()
    for cid, url in peers.items():
        if cid == "evil-cat" and "cat-3" in peers:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        role_label = "Evil Cat (:8005)" if cid in ("cat-3", "evil-cat") else f"Agent {cid}"
        nodes.append({"id": cid, "label": f"{cid} — {role_label}", "role": "agent", "url": url})

    return nodes


async def fetch_cluster_sre_snapshot(local_node_id: Optional[str] = None) -> Dict[str, Any]:
    """Polls `/api/sre/status` from all cluster nodes and builds a unified SRE snapshot."""
    nodes = discover_cluster_nodes()
    local_store = get_sre_store()
    local_summary = local_store.get_summary(include_spans=True, span_limit=80)

    node_snapshots: Dict[str, Any] = {}
    all_spans: List[Dict[str, Any]] = []
    all_logs: List[Dict[str, Any]] = []

    total_tpm_60s = 0
    total_input_tpm_60s = 0
    total_output_tpm_60s = 0
    total_thinking_tpm_60s = 0
    total_rpm_60s = 0
    total_429_errors = 0
    total_timeouts = 0
    total_cache_hits = 0
    total_cache_misses = 0

    async with httpx.AsyncClient(timeout=1.5) as client:
        for node in nodes:
            nid = node["id"]
            nurl = node["url"]
            if nid == local_node_id or (
                local_summary["agent_id"] and nid == local_summary["agent_id"]
            ) or (nid == "mcp-server" and local_summary["service_name"] == "cm-mcp-server") or (
                nid == "game-server" and local_summary["service_name"] == "cm-game-server"
            ):
                snap = dict(local_summary)
                snap["online"] = True
                snap["node_id"] = nid
                snap["label"] = node["label"]
                snap["url"] = nurl
                node_snapshots[nid] = snap
            else:
                try:
                    resp = await client.get(f"{nurl}/api/sre/status")
                    if resp.status_code == 200:
                        snap = resp.json()
                        snap["online"] = True
                        snap["node_id"] = nid
                        snap["label"] = node["label"]
                        snap["url"] = nurl
                        node_snapshots[nid] = snap
                    else:
                        node_snapshots[nid] = {
                            "online": False,
                            "node_id": nid,
                            "label": node["label"],
                            "url": nurl,
                            "error": f"HTTP {resp.status_code}",
                        }
                except Exception as exc:
                    node_snapshots[nid] = {
                        "online": False,
                        "node_id": nid,
                        "label": node["label"],
                        "url": nurl,
                        "error": str(exc),
                    }

    for nid, snap in node_snapshots.items():
        if not snap.get("online"):
            continue
        g = snap.get("genai") or {}
        t = snap.get("tools") or {}
        total_tpm_60s += int(g.get("tpm_60s", 0))
        total_input_tpm_60s += int(g.get("input_tpm_60s", 0))
        total_output_tpm_60s += int(g.get("output_tpm_60s", 0))
        total_thinking_tpm_60s += int(g.get("thinking_tpm_60s", 0))
        total_rpm_60s += int(g.get("rpm_60s", 0))
        total_429_errors += int(g.get("errors_429_total", 0))
        total_timeouts += int(g.get("errors_timeout_total", 0))
        total_cache_hits += int(t.get("cache_hits_total", 0))
        total_cache_misses += int(t.get("cache_misses_total", 0))

        for sp in snap.get("spans") or []:
            sp_copy = dict(sp)
            sp_copy["node_id"] = nid
            all_spans.append(sp_copy)

        for lg in snap.get("logs") or []:
            lg_copy = dict(lg)
            lg_copy["node_id"] = nid
            all_logs.append(lg_copy)

    all_spans.sort(key=lambda x: x.get("start_time_ns") or 0, reverse=True)
    all_logs.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    total_cache_ops = total_cache_hits + total_cache_misses
    global_cache_hit_pct = round((total_cache_hits / total_cache_ops) * 100.0, 1) if total_cache_ops > 0 else 0.0

    gs_sim = (node_snapshots.get("game-server") or {}).get("simulation") or local_summary.get("simulation") or {}

    return {
        "timestamp": time.time(),
        "config": get_otel_config(),
        "global": {
            "online_nodes": sum(1 for s in node_snapshots.values() if s.get("online")),
            "total_nodes": len(nodes),
            "tpm_60s": total_tpm_60s,
            "input_tpm_60s": total_input_tpm_60s,
            "output_tpm_60s": total_output_tpm_60s,
            "thinking_tpm_60s": total_thinking_tpm_60s,
            "rpm_60s": total_rpm_60s,
            "errors_429_total": total_429_errors,
            "errors_timeout_total": total_timeouts,
            "cache_hits_total": total_cache_hits,
            "cache_misses_total": total_cache_misses,
            "cache_hit_pct": global_cache_hit_pct,
            "tick_p50_ms": gs_sim.get("tick_p50_ms", 0.0),
            "tick_p95_ms": gs_sim.get("tick_p95_ms", 0.0),
            "tick_overruns_total": gs_sim.get("tick_overruns_total", 0),
        },
        "nodes": node_snapshots,
        "spans": all_spans[:150],
        "logs": all_logs[:100],
    }


async def broadcast_otel_config_update(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Applies runtime OTel/logging config locally and broadcasts to all cluster nodes if `broadcast=True`."""
    updated = update_otel_config(
        tracing_enabled=payload.get("tracing_enabled"),
        file_logging_enabled=payload.get("file_logging_enabled"),
        console_log_level=payload.get("console_log_level"),
        backend=payload.get("backend"),
        otlp_endpoint=payload.get("otlp_endpoint"),
    )
    if payload.get("broadcast", True):
        nodes = discover_cluster_nodes()
        forward_body = {
            "tracing_enabled": updated["tracing_enabled"],
            "file_logging_enabled": updated["file_logging_enabled"],
            "console_log_level": updated["console_log_level"],
            "backend": updated["backend"],
            "otlp_endpoint": updated["otlp_endpoint"],
            "broadcast": False,
        }
        async with httpx.AsyncClient(timeout=1.2) as client:
            for n in nodes:
                try:
                    await client.post(f"{n['url']}/api/sre/config", json=forward_body)
                except Exception:
                    pass
    return updated


SRE_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Cat & Mouse — OpenTelemetry SRE Observability Mission Control</title>
  <style>
    :root {
      --bg: #f8fafc;
      --panel: #ffffff;
      --panel-alt: #f1f5f9;
      --border: #e2e8f0;
      --text: #0f172a;
      --muted: #64748b;
      --accent: #0284c7;
      --green: #16a34a;
      --yellow: #ca8a04;
      --red: #dc2626;
      --purple: #7c3aed;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
      background: var(--bg);
      color: var(--text);
      font-size: 13px;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      padding: 12px 20px;
      background: #ffffff;
      border-bottom: 1px solid var(--border);
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
      gap: 12px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 16px;
      font-weight: 700;
      color: var(--text);
    }
    .nav-links a {
      color: var(--muted);
      text-decoration: none;
      margin-right: 14px;
      padding: 5px 10px;
      border-radius: 6px;
      border: 1px solid transparent;
      font-weight: 500;
    }
    .nav-links a:hover, .nav-links a.active {
      color: var(--accent);
      border-color: #bae6fd;
      background: #f0f9ff;
    }
    .controls-bar {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      background: var(--panel-alt);
      padding: 8px 14px;
      border-radius: 8px;
      border: 1px solid var(--border);
    }
    .toggle-btn {
      cursor: pointer;
      padding: 5px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: #ffffff;
      color: var(--text);
      font-weight: 600;
      font-size: 12px;
    }
    .toggle-btn.on {
      border-color: #86efac;
      background: #dcfce7;
      color: #15803d;
    }
    .toggle-btn.off {
      border-color: #fca5a5;
      background: #fee2e2;
      color: #b91c1c;
    }
    select, input[type="text"] {
      background: #ffffff;
      color: var(--text);
      border: 1px solid var(--border);
      padding: 4px 8px;
      border-radius: 5px;
      font-size: 12px;
    }
    main {
      padding: 16px 20px;
      max-width: 1650px;
      margin: 0 auto;
    }
    .golden-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .metric-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
    }
    .metric-title {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 6px;
      font-weight: 600;
    }
    .metric-value {
      font-size: 22px;
      font-weight: 700;
      color: var(--text);
    }
    .metric-sub {
      color: var(--muted);
      font-size: 11px;
      margin-top: 4px;
    }
    .tabs {
      display: flex;
      gap: 8px;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }
    .tab-btn {
      cursor: pointer;
      padding: 7px 14px;
      background: var(--panel);
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 6px;
      font-weight: 600;
    }
    .tab-btn.active {
      background: #e0f2fe;
      color: #0369a1;
      border-color: #7dd3fc;
    }
    .nodes-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 16px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
    }
    .panel h3 {
      margin: 0 0 10px 0;
      font-size: 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--text);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    th, td {
      text-align: left;
      padding: 6px 8px;
      border-bottom: 1px solid #f1f5f9;
    }
    th { color: var(--muted); font-weight: 600; background: #f8fafc; }
    code {
      background: #f1f5f9;
      color: #0f172a;
      padding: 1px 5px;
      border-radius: 4px;
    }
    .badge {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge.ok { background: #dcfce7; color: #15803d; }
    .badge.err { background: #fee2e2; color: #b91c1c; }
    .badge.warn { background: #fef9c3; color: #a16207; }
    .badge.info { background: #e0f2fe; color: #0369a1; }
    .waterfall-bar-wrap {
      width: 180px;
      background: #e2e8f0;
      height: 12px;
      border-radius: 3px;
      overflow: hidden;
      position: relative;
    }
    .waterfall-bar {
      height: 100%;
      background: linear-gradient(90deg, #0284c7, #7c3aed);
      border-radius: 3px;
    }
    .two-col {
      display: grid;
      grid-template-columns: 1.3fr 1fr;
      gap: 14px;
    }
    @media (max-width: 1100px) {
      .two-col { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span>🔭 Cat & Mouse — OTel SRE Observability Mission Control</span>
      <nav class="nav-links">
        <a href="http://127.0.0.1:8000">🏟️ Arena (:8000)</a>
        <a href="http://127.0.0.1:8000/boxscore">📊 Boxscore</a>
        <a href="http://127.0.0.1:9050">🔌 MCP Hub (:9050)</a>
        <a href="/sre" class="active">📈 SRE Dashboard</a>
        <a href="/metrics" target="_blank">🔥 Prometheus /metrics</a>
      </nav>
    </div>
    <div class="controls-bar">
      <span><strong>Runtime Switches:</strong></span>
      <button id="btn-tracing" class="toggle-btn on" onclick="toggleConfig('tracing')">OTel Tracing: ON</button>
      <button id="btn-filelog" class="toggle-btn off" onclick="toggleConfig('filelog')">Disk JSONL Log: OFF</button>
      <label>Log Level:
        <select id="sel-loglevel" onchange="changeLogLevel(this.value)">
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING" selected>WARNING</option>
          <option value="ERROR">ERROR</option>
        </select>
      </label>
      <label>Exporter Destination:
        <select id="sel-backend" onchange="changeBackend(this.value)">
          <option value="local">Local SRE Hub + /metrics</option>
          <option value="otlp">OTLP Collector / Jaeger</option>
          <option value="gcp">Google Cloud Trace / GMP</option>
        </select>
      </label>
    </div>
  </header>

  <main>
    <!-- Golden Signals Strip -->
    <div class="golden-grid">
      <div class="metric-card">
        <div class="metric-title">Cluster Nodes Online</div>
        <div class="metric-value" id="gs-nodes">- / -</div>
        <div class="metric-sub" id="gs-backend-sub">Destination: local</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Sim Tick Clock (p50 / p95)</div>
        <div class="metric-value" id="gs-tick">- ms</div>
        <div class="metric-sub" id="gs-overruns">Overruns: 0</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">60s Rolling Token Burn (TPM)</div>
        <div class="metric-value" id="gs-tpm">0</div>
        <div class="metric-sub" id="gs-tpm-sub">In: 0 | Out: 0 | Think: 0</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">60s Rolling Request Rate (RPM)</div>
        <div class="metric-value" id="gs-rpm">0</div>
        <div class="metric-sub">Across all Actors & Coaches</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">429 Quota / Timeout Errors</div>
        <div class="metric-value" id="gs-errors">0 / 0</div>
        <div class="metric-sub">429 Rate Limits / Timeouts</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">MCP Tool Cache Hit Ratio</div>
        <div class="metric-value" id="gs-cache">0%</div>
        <div class="metric-sub" id="gs-cache-sub">Hits: 0 | Misses: 0</div>
      </div>
    </div>

    <!-- Filter Tabs -->
    <div class="tabs" id="node-tabs">
      <button class="tab-btn active" onclick="selectNode('all')">🌐 All Services & Agents</button>
    </div>

    <!-- Per-Node SLI Cards -->
    <div class="nodes-grid" id="nodes-container"></div>

    <!-- Tool Telemetry & Logging Latency Impact -->
    <div class="two-col">
      <div class="panel">
        <h3>
          <span>🛠️ Coach & MCP Tool Call Telemetry (`@traced_tool`)</span>
          <span class="badge info" id="tool-count-badge">0 calls</span>
        </h3>
        <table>
          <thead>
            <tr>
              <th>Node</th>
              <th>Tool Name</th>
              <th>Calls</th>
              <th>Cache Hit %</th>
              <th>p50 Latency</th>
              <th>p95 Latency</th>
              <th>Errors</th>
            </tr>
          </thead>
          <tbody id="tools-tbody"></tbody>
        </table>
      </div>

      <div class="panel">
        <h3>
          <span>⚡ Logging Overhead & Token Usage Breakdown</span>
          <span class="badge ok">Live A/B Check</span>
        </h3>
        <table>
          <thead>
            <tr>
              <th>Agent / Service</th>
              <th>Input Tok</th>
              <th>Output Tok</th>
              <th>Think Tok</th>
              <th>Log ON p50</th>
              <th>Log OFF p50</th>
            </tr>
          </thead>
          <tbody id="tokens-tbody"></tbody>
        </table>
      </div>
    </div>

    <!-- Distributed Trace Spans & Correlated Logs -->
    <div class="two-col">
      <div class="panel">
        <h3>
          <span>🌊 Live OpenTelemetry Trace Spans (`sim.tick` ➔ `agent.act` ➔ `genai.*` ➔ `mcp.tool.*`)</span>
          <span class="badge info" id="span-count-badge">0 spans</span>
        </h3>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Service / Agent</th>
              <th>Span Name</th>
              <th>Tick</th>
              <th>Duration</th>
              <th>Waterfall</th>
              <th>Trace ID</th>
            </tr>
          </thead>
          <tbody id="spans-tbody"></tbody>
        </table>
      </div>

      <div class="panel">
        <h3>
          <span>📜 Correlated Structured Logs (`trace_id` / `span_id`)</span>
          <span class="badge info" id="log-count-badge">0 logs</span>
        </h3>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Node</th>
              <th>Level</th>
              <th>Message</th>
              <th>Trace ID</th>
            </tr>
          </thead>
          <tbody id="logs-tbody"></tbody>
        </table>
      </div>
    </div>
  </main>

  <script>
    let currentFilter = 'all';
    let latestData = null;

    function selectNode(nodeId) {
      currentFilter = nodeId;
      renderDashboard();
    }

    async function toggleConfig(which) {
      if (!latestData || !latestData.config) return;
      const cfg = latestData.config;
      const body = { broadcast: true };
      if (which === 'tracing') body.tracing_enabled = !cfg.tracing_enabled;
      if (which === 'filelog') body.file_logging_enabled = !cfg.file_logging_enabled;
      await fetch('/api/sre/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      await pollSRE();
    }

    async function changeLogLevel(level) {
      await fetch('/api/sre/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ console_log_level: level, broadcast: true }),
      });
      await pollSRE();
    }

    async function changeBackend(backend) {
      await fetch('/api/sre/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ backend: backend, broadcast: true }),
      });
      await pollSRE();
    }

    function renderDashboard() {
      if (!latestData) return;
      const g = latestData.global || {};
      const cfg = latestData.config || {};
      const nodes = latestData.nodes || {};

      // Controls
      const btnT = document.getElementById('btn-tracing');
      btnT.textContent = `OTel Tracing: ${cfg.tracing_enabled ? 'ON' : 'OFF'}`;
      btnT.className = `toggle-btn ${cfg.tracing_enabled ? 'on' : 'off'}`;

      const btnF = document.getElementById('btn-filelog');
      btnF.textContent = `Disk JSONL Log: ${cfg.file_logging_enabled ? 'ON' : 'OFF'}`;
      btnF.className = `toggle-btn ${cfg.file_logging_enabled ? 'on' : 'off'}`;

      if (cfg.console_log_level) {
        document.getElementById('sel-loglevel').value = cfg.console_log_level;
      }
      if (cfg.backend) {
        document.getElementById('sel-backend').value = cfg.backend;
      }

      // Golden signals
      document.getElementById('gs-nodes').textContent = `${g.online_nodes || 0} / ${g.total_nodes || 0}`;
      document.getElementById('gs-backend-sub').textContent = `Destination: ${cfg.backend || 'local'}`;
      document.getElementById('gs-tick').textContent = `${g.tick_p50_ms || 0} / ${g.tick_p95_ms || 0} ms`;
      document.getElementById('gs-overruns').textContent = `Tick Overruns: ${g.tick_overruns_total || 0}`;
      document.getElementById('gs-tpm').textContent = (g.tpm_60s || 0).toLocaleString();
      document.getElementById('gs-tpm-sub').textContent = `In: ${(g.input_tpm_60s || 0).toLocaleString()} | Out: ${(g.output_tpm_60s || 0).toLocaleString()} | Think: ${(g.thinking_tpm_60s || 0).toLocaleString()}`;
      document.getElementById('gs-rpm').textContent = `${g.rpm_60s || 0} req/min`;
      document.getElementById('gs-errors').textContent = `${g.errors_429_total || 0} / ${g.errors_timeout_total || 0}`;
      document.getElementById('gs-cache').textContent = `${g.cache_hit_pct || 0}%`;
      document.getElementById('gs-cache-sub').textContent = `Hits: ${g.cache_hits_total || 0} | Misses: ${g.cache_misses_total || 0}`;

      // Tabs
      const tabsEl = document.getElementById('node-tabs');
      let tabsHtml = `<button class="tab-btn ${currentFilter === 'all' ? 'active' : ''}" onclick="selectNode('all')">🌐 All Services & Agents</button>`;
      for (const [nid, n] of Object.entries(nodes)) {
        const dot = n.online ? '🟢' : '🔴';
        tabsHtml += `<button class="tab-btn ${currentFilter === nid ? 'active' : ''}" onclick="selectNode('${nid}')">${dot} ${n.label || nid}</button>`;
      }
      tabsEl.innerHTML = tabsHtml;

      // Node Cards
      const nodesEl = document.getElementById('nodes-container');
      let cardsHtml = '';
      for (const [nid, n] of Object.entries(nodes)) {
        if (currentFilter !== 'all' && currentFilter !== nid) continue;
        if (!n.online) {
          cardsHtml += `<div class="panel"><h3><span>🔴 ${n.label || nid}</span><span class="badge err">OFFLINE</span></h3><p>Unreachable at ${n.url}: ${n.error || ''}</p></div>`;
          continue;
        }
        const ng = n.genai || {};
        const act = ng.actor || {};
        const coach = ng.coach || {};
        const sim = n.simulation || {};
        const ds = ng.decision_sources || {};
        const dsStr = Object.entries(ds).map(([k, v]) => `${k}: ${v}`).join(' | ') || 'none yet';

        cardsHtml += `
          <div class="panel">
            <h3>
              <span>🟢 ${n.label || nid}</span>
              <span class="badge ok">Uptime ${n.uptime_sec || 0}s</span>
            </h3>
            <table>
              <tbody>
                ${nid === 'game-server' ? `
                  <tr><th>Sim Ticks Recorded</th><td>${sim.ticks_recorded || 0}</td><th>Tick p50 / p95</th><td>${sim.tick_p50_ms || 0} / ${sim.tick_p95_ms || 0} ms</td></tr>
                  <tr><th>Tick Overruns</th><td>${sim.tick_overruns_total || 0}</td><th>Stale Drops</th><td>${sim.stale_drops_total || 0}</td></tr>
                ` : `
                  <tr><th>Actor TTFT (p50 / p95)</th><td>${act.ttft_p50_ms || 0} / ${act.ttft_p95_ms || 0} ms</td><th>Actor Latency (p50 / p95)</th><td>${act.latency_p50_ms || 0} / ${act.latency_p95_ms || 0} ms</td></tr>
                  <tr><th>Coach TTFT (p50 / p95)</th><td>${coach.ttft_p50_ms || 0} / ${coach.ttft_p95_ms || 0} ms</td><th>Coach Latency (p50 / p95)</th><td>${coach.latency_p50_ms || 0} / ${coach.latency_p95_ms || 0} ms</td></tr>
                  <tr><th>60s Rolling TPM / RPM</th><td>${(ng.tpm_60s || 0).toLocaleString()} TPM (${ng.rpm_60s || 0} RPM)</td><th>Coach Avg Tools/Eval</th><td>${coach.avg_tools_per_eval || 0}</td></tr>
                  <tr><th>Decision Sources</th><td colspan="3">${dsStr}</td></tr>
                `}
              </tbody>
            </table>
          </div>
        `;
      }
      nodesEl.innerHTML = cardsHtml;

      // Tool Table & Token Table
      let toolsHtml = '';
      let totalToolCalls = 0;
      let tokensHtml = '';

      for (const [nid, n] of Object.entries(nodes)) {
        if (!n.online) continue;
        if (currentFilter !== 'all' && currentFilter !== nid) continue;
        const byName = (n.tools && n.tools.by_name) || {};
        for (const [tname, tst] of Object.entries(byName)) {
          totalToolCalls += tst.calls || 0;
          toolsHtml += `
            <tr>
              <td><strong>${nid}</strong></td>
              <td><code>${tname}</code></td>
              <td>${tst.calls || 0}</td>
              <td>${tst.cache_hit_pct || 0}% (${tst.cache_hits || 0})</td>
              <td>${tst.p50_ms || 0} ms</td>
              <td>${tst.p95_ms || 0} ms</td>
              <td>${tst.errors ? `<span class="badge err">${tst.errors}</span>` : '0'}</td>
            </tr>
          `;
        }
        const ng = n.genai || {};
        const impact = ng.logging_latency_impact || {};
        tokensHtml += `
          <tr>
            <td><strong>${nid}</strong></td>
            <td>${(ng.input_tokens_total || 0).toLocaleString()}</td>
            <td>${(ng.output_tokens_total || 0).toLocaleString()}</td>
            <td>${(ng.thinking_tokens_total || 0).toLocaleString()}</td>
            <td>${impact.logging_on_p50_ms || 0} ms (n=${impact.logging_on_samples || 0})</td>
            <td>${impact.logging_off_p50_ms || 0} ms (n=${impact.logging_off_samples || 0})</td>
          </tr>
        `;
      }
      document.getElementById('tool-count-badge').textContent = `${totalToolCalls} calls`;
      document.getElementById('tools-tbody').innerHTML = toolsHtml || '<tr><td colspan="7" style="color:var(--muted)">No tool calls recorded yet</td></tr>';
      document.getElementById('tokens-tbody').innerHTML = tokensHtml || '<tr><td colspan="6" style="color:var(--muted)">No token usage recorded yet</td></tr>';

      // Spans Table
      const spans = (latestData.spans || []).filter(s => currentFilter === 'all' || s.node_id === currentFilter || s.agent_id === currentFilter);
      document.getElementById('span-count-badge').textContent = `${spans.length} spans`;
      const maxDur = Math.max(100, ...spans.slice(0, 40).map(s => s.duration_ms || 1));
      let spansHtml = '';
      for (const s of spans.slice(0, 40)) {
        const pct = Math.min(100, Math.max(3, Math.round(((s.duration_ms || 0) / maxDur) * 100)));
        const badgeClass = s.status === 'ERROR' ? 'err' : 'ok';
        spansHtml += `
          <tr>
            <td>${s.time_str || ''}</td>
            <td><span class="badge info">${s.node_id || s.service_name}</span></td>
            <td><code>${s.name}</code></td>
            <td>${s.tick != null ? s.tick : '-'}</td>
            <td><span class="badge ${badgeClass}">${s.duration_ms || 0} ms</span></td>
            <td><div class="waterfall-bar-wrap"><div class="waterfall-bar" style="width:${pct}%"></div></div></td>
            <td><code>${(s.trace_id || '').slice(0, 12)}…</code></td>
          </tr>
        `;
      }
      document.getElementById('spans-tbody').innerHTML = spansHtml || '<tr><td colspan="7" style="color:var(--muted)">No spans recorded yet</td></tr>';

      // Logs Table
      const logs = (latestData.logs || []).filter(l => currentFilter === 'all' || l.node_id === currentFilter || l.agent_id === currentFilter);
      document.getElementById('log-count-badge').textContent = `${logs.length} logs`;
      let logsHtml = '';
      for (const l of logs.slice(0, 35)) {
        const b = l.level === 'ERROR' ? 'err' : (l.level === 'WARNING' ? 'warn' : 'ok');
        logsHtml += `
          <tr>
            <td>${l.time_str || ''}</td>
            <td><strong>${l.node_id || l.service}</strong></td>
            <td><span class="badge ${b}">${l.level}</span></td>
            <td>${l.message || ''}</td>
            <td><code>${(l.trace_id || '').slice(0, 10)}</code></td>
          </tr>
        `;
      }
      document.getElementById('logs-tbody').innerHTML = logsHtml || '<tr><td colspan="5" style="color:var(--muted)">No correlated logs recorded yet</td></tr>';
    }

    async function pollSRE() {
      try {
        const res = await fetch('/api/sre/cluster');
        if (res.ok) {
          latestData = await res.json();
          renderDashboard();
        }
      } catch (e) {
        console.warn('SRE poll error:', e);
      }
    }

    pollSRE();
    setInterval(pollSRE, 2000);
  </script>
</body>
</html>
"""
