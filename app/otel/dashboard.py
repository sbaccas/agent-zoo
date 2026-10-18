"""Unified SRE Dashboard HTML & cluster aggregation helpers for Cat & Mouse."""

from datetime import datetime
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from app.otel.setup import get_otel_config, get_sre_store, update_otel_config


SERVICE_PORT_LABELS: Dict[str, str] = {
    "game-server": ":8000 Game Server",
    "cm-game-server": ":8000 Game Server",
    "mcp-server": ":9050 MCP Server",
    "cm-mcp-server": ":9050 MCP Server",
    "cat-1": ":8001 Cat-1 Agent",
    "cm-agent-cat-1": ":8001 Cat-1 Agent",
    "cat-2": ":8003 Cat-2 Agent",
    "cm-agent-cat-2": ":8003 Cat-2 Agent",
    "cat-3": ":8005 Cat-3 (Evil Cat)",
    "cm-agent-cat-3": ":8005 Cat-3 (Evil Cat)",
}


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


def build_trace_trees(all_spans: List[Dict[str, Any]], max_traces: int = 60) -> List[Dict[str, Any]]:
    """Groups flat OpenTelemetry spans by `trace_id` into hierarchical Cloud Trace waterfall stories."""
    by_trace: Dict[str, List[Dict[str, Any]]] = {}
    for sp in all_spans:
        tid = sp.get("trace_id") or ""
        if not tid or tid == "0" * 32:
            continue
        by_trace.setdefault(tid, []).append(sp)

    traces_out: List[Dict[str, Any]] = []

    for tid, raw_spans in by_trace.items():
        # Deduplicate by span_id while preserving order
        span_by_id: Dict[str, Dict[str, Any]] = {}
        for sp in sorted(raw_spans, key=lambda s: (s.get("start_time_ns") or 0, -(s.get("duration_ms") or 0))):
            sid = sp.get("span_id") or ""
            if sid and sid not in span_by_id:
                span_by_id[sid] = dict(sp)

        if not span_by_id:
            continue

        # Step A: Reconnect parent_span_id for any backfilled / partially-evicted / in-flight spans
        coach_or_actor_root = next(
            (
                sp for sp in span_by_id.values()
                if any(k in (sp.get("name") or "") for k in ("genai.coach_eval", "coach.evaluate", "genai.actor_deliberation"))
            ),
            None,
        )
        client_tool_spans = [
            sp for sp in span_by_id.values()
            if (sp.get("name") or "").startswith("mcp.tool.")
        ]

        # If a Coach or Actor trace is currently in-flight (or its outer root span aged out),
        # synthesize the outer root bar spanning [min_start, max_end] so the Gantt chart has a single 100% root
        if not coach_or_actor_root and len(span_by_id) >= 2:
            s_names = [sp.get("name") or "" for sp in span_by_id.values()]
            is_coach_like = any(
                any(k in n for k in ("coach.", "execute_tool", "mcp.tool.", "tools/call", "mcp."))
                for n in s_names
            )
            is_actor_like = any("actor." in n for n in s_names)
            if is_coach_like or is_actor_like:
                ref_sp = next(
                    (sp for sp in span_by_id.values() if sp.get("node_id") != "mcp-server"),
                    next(iter(span_by_id.values())),
                )
                ref_starts = [s.get("start_time_ns") or 0 for s in span_by_id.values() if s.get("start_time_ns")]
                ref_ends = [
                    (s.get("end_time_ns") or ((s.get("start_time_ns") or 0) + int((s.get("duration_ms") or 0) * 1_000_000)))
                    for s in span_by_id.values()
                    if s.get("start_time_ns")
                ]
                r_start = min(ref_starts) if ref_starts else 0
                r_end = max(ref_ends) if ref_ends else r_start
                r_dur = round(max(0.01, (r_end - r_start) / 1_000_000.0), 2)
                syn_root_sid = f"{tid[:12]}root"
                r_node = ref_sp.get("node_id") if ref_sp.get("node_id") != "mcp-server" else ((ref_sp.get("attributes") or {}).get("cm.agent_id") or "cat-2")
                r_svc = ref_sp.get("service_name") if ref_sp.get("node_id") != "mcp-server" else f"cm-agent-{r_node}"
                coach_or_actor_root = {
                    "trace_id": tid,
                    "span_id": syn_root_sid,
                    "parent_span_id": None,
                    "name": "genai.coach_eval" if is_coach_like else "genai.actor_deliberation",
                    "kind": "CLIENT",
                    "service_name": r_svc,
                    "agent_id": r_node,
                    "node_id": r_node,
                    "start_time_ns": r_start,
                    "end_time_ns": r_end,
                    "duration_ms": r_dur,
                    "time_str": ref_sp.get("time_str") or "",
                    "status": "OK",
                    "attributes": {
                        "gen_ai.system": "google_genai",
                        "gen_ai.role": "coach" if is_coach_like else "actor",
                        "gen_ai.request.model": "gemini-2.5-flash",
                    },
                }
                span_by_id[syn_root_sid] = coach_or_actor_root

        # Link server-side `mcp.<tool>` / `tools/call <tool>` spans to matching client-side `mcp.tool.<tool>` spans if parent missing
        for sp in span_by_id.values():
            pid = sp.get("parent_span_id")
            sname = sp.get("name") or ""
            is_server_mcp = (sname.startswith("mcp.") and not sname.startswith("mcp.tool.")) or sname.startswith("tools/call ")
            if (not pid or pid not in span_by_id) and is_server_mcp:
                short_tool = sname[len("mcp."):] if sname.startswith("mcp.") else sname[len("tools/call "):]
                s_start = sp.get("start_time_ns") or 0
                best_client = None
                best_dist = 10_000_000_000  # 10s in ns
                for csp in client_tool_spans:
                    cname = csp.get("name") or ""
                    if cname.endswith(short_tool) and csp.get("span_id") != sp.get("span_id"):
                        dist = abs((csp.get("start_time_ns") or 0) - s_start)
                        if dist < best_dist:
                            best_dist = dist
                            best_client = csp
                if best_client:
                    sp["parent_span_id"] = best_client.get("span_id")
                elif coach_or_actor_root:
                    sp["parent_span_id"] = coach_or_actor_root.get("span_id")

        if coach_or_actor_root:
            root_sid = coach_or_actor_root.get("span_id")
            for sp in span_by_id.values():
                if sp is coach_or_actor_root:
                    continue
                pid = sp.get("parent_span_id")
                if not pid or pid not in span_by_id:
                    sp["parent_span_id"] = root_sid

        # Step B: Partition untraced LLM sub-turn gaps in multi-turn Coach / MCP traces
        has_explicit_llm_turns = any(
            any(k in (s.get("name") or "") for k in ("llm_turn", "call_llm", "generate_content"))
            for s in span_by_id.values()
        )
        tool_like_spans = [
            sp for sp in span_by_id.values()
            if (sp.get("name") or "").startswith("mcp.tool.")
            or (
                ((sp.get("name") or "").startswith("mcp.") or (sp.get("name") or "").startswith("tools/call"))
                and (sp.get("parent_span_id") not in span_by_id or sp.get("parent_span_id") == (coach_or_actor_root or {}).get("span_id"))
            )
        ]

        if not has_explicit_llm_turns and (coach_or_actor_root or len(tool_like_spans) >= 2):
            host_span = coach_or_actor_root or (client_tool_spans[0] if client_tool_spans else next(iter(span_by_id.values())))
            host_node_id = host_span.get("node_id") or host_span.get("agent_id") or "cat-1"
            if host_node_id == "mcp-server":
                # Infer calling agent if only mcp-server spans survived
                host_node_id = (host_span.get("attributes") or {}).get("cm.agent_id") or "cat-2"
            host_service = host_span.get("service_name") if host_span.get("node_id") != "mcp-server" else f"cm-agent-{host_node_id}"
            host_agent = host_span.get("agent_id") if host_span.get("node_id") != "mcp-server" else host_node_id
            root_attrs = (coach_or_actor_root or {}).get("attributes") or {}
            model_val = root_attrs.get("gen_ai.request.model") or "gemini-2.5-flash"

            all_starts = [s.get("start_time_ns") or 0 for s in span_by_id.values() if s.get("start_time_ns")]
            all_ends = [
                (s.get("end_time_ns") or ((s.get("start_time_ns") or 0) + int((s.get("duration_ms") or 0) * 1_000_000)))
                for s in span_by_id.values()
                if s.get("start_time_ns")
            ]
            t_min_ns = (coach_or_actor_root.get("start_time_ns") if coach_or_actor_root else None) or (min(all_starts) if all_starts else 0)
            t_max_ns = (coach_or_actor_root.get("end_time_ns") if coach_or_actor_root else None) or (max(all_ends) if all_ends else t_min_ns)
            if all_starts:
                t_min_ns = min(t_min_ns, min(all_starts))
            if all_ends:
                t_max_ns = max(t_max_ns, max(all_ends))

            # Build merged tool execution batches (clustering parallel tool calls within 150ms)
            raw_intervals = []
            for tsp in sorted(tool_like_spans, key=lambda x: x.get("start_time_ns") or 0):
                ts_s = tsp.get("start_time_ns") or 0
                ts_e = tsp.get("end_time_ns") or (ts_s + int((tsp.get("duration_ms") or 0) * 1_000_000))
                t_short = (tsp.get("name") or "").replace("mcp.tool.", "").replace("mcp.", "")
                if ts_s > 0 and ts_e >= ts_s:
                    raw_intervals.append([ts_s, ts_e, [t_short]])

            batches = []
            for iv in raw_intervals:
                if not batches:
                    batches.append(iv)
                else:
                    prev = batches[-1]
                    # Within 150ms = same parallel tool execution batch
                    if iv[0] <= prev[1] + 150_000_000:
                        prev[1] = max(prev[1], iv[1])
                        if iv[2][0] not in prev[2]:
                            prev[2].append(iv[2][0])
                    else:
                        batches.append(iv)

            gap_windows = []
            min_gap_ns = 40_000_000  # 40ms threshold
            if batches:
                if batches[0][0] - t_min_ns >= min_gap_ns:
                    gap_windows.append((t_min_ns, batches[0][0], "initial_prompt_and_tool_selection", ",".join(batches[0][2])))
                for b_idx in range(len(batches) - 1):
                    g_s = batches[b_idx][1]
                    g_e = batches[b_idx + 1][0]
                    if g_e - g_s >= min_gap_ns:
                        gap_windows.append((g_s, g_e, "tool_result_synthesis_and_reasoning", ",".join(batches[b_idx + 1][2])))
                if t_max_ns - batches[-1][1] >= min_gap_ns:
                    gap_windows.append((batches[-1][1], t_max_ns, "final_directive_generation", "final_directive"))
            elif coach_or_actor_root and (t_max_ns - t_min_ns >= min_gap_ns) and len(span_by_id) == 1:
                gap_windows.append((t_min_ns, t_max_ns, "single_turn_generation", "decision"))

            parent_sid_for_turns = coach_or_actor_root.get("span_id") if coach_or_actor_root else None
            for turn_i, (g_s, g_e, phase_desc, req_tools) in enumerate(gap_windows, start=1):
                syn_sid = f"{tid[:10]}turn{turn_i:02d}"
                dur_ms_val = round((g_e - g_s) / 1_000_000.0, 2)
                span_by_id[syn_sid] = {
                    "trace_id": tid,
                    "span_id": syn_sid,
                    "parent_span_id": parent_sid_for_turns,
                    "name": f"genai.llm_turn.{turn_i}",
                    "kind": "CLIENT",
                    "service_name": host_service,
                    "agent_id": host_agent,
                    "node_id": host_node_id,
                    "start_time_ns": g_s,
                    "end_time_ns": g_e,
                    "duration_ms": dur_ms_val,
                    "status": "OK",
                    "attributes": {
                        "gen_ai.system": "google_genai",
                        "gen_ai.request.model": model_val,
                        "gen_ai.turn_index": turn_i,
                        "cm.turn_phase": phase_desc,
                        "cm.requested_tools": req_tools,
                    },
                }

        spans_list = list(span_by_id.values())
        if not spans_list:
            continue

        children_map: Dict[str, List[Dict[str, Any]]] = {}
        roots: List[Dict[str, Any]] = []

        for sp in spans_list:
            pid = sp.get("parent_span_id")
            if pid and pid in span_by_id and pid != sp.get("span_id"):
                children_map.setdefault(pid, []).append(sp)
            else:
                roots.append(sp)

        for pid in children_map:
            children_map[pid].sort(key=lambda s: (s.get("start_time_ns") or 0, -(s.get("duration_ms") or 0)))
        roots.sort(key=lambda s: (s.get("start_time_ns") or 0, -(s.get("duration_ms") or 0)))

        # Compute trace time bounds
        start_times = [s.get("start_time_ns") or 0 for s in spans_list if s.get("start_time_ns")]
        end_times = [
            (s.get("end_time_ns") or ((s.get("start_time_ns") or 0) + int((s.get("duration_ms") or 0) * 1_000_000)))
            for s in spans_list
            if s.get("start_time_ns")
        ]
        trace_start_ns = min(start_times) if start_times else 0
        trace_end_ns = max(end_times) if end_times else trace_start_ns
        wall_duration_ms = round(max(0.0, (trace_end_ns - trace_start_ns) / 1_000_000.0), 2)
        max_single_dur = max((s.get("duration_ms") or 0.0) for s in spans_list)
        trace_duration_ms = round(max(wall_duration_ms, max_single_dur, 0.01), 2)

        ordered_spans: List[Dict[str, Any]] = []
        visited = set()

        def _dfs(node: Dict[str, Any], depth: int, ancestor_pipes: List[bool], is_last: bool, parent_node_id: Optional[str]) -> None:
            sid = node.get("span_id")
            if sid in visited:
                return
            visited.add(sid)

            # Build exact ASCII tree prefix matching @[code_block]
            prefix = ""
            for has_more_siblings in ancestor_pipes:
                prefix += "│   " if has_more_siblings else "    "
            prefix += "└─ " if is_last else "├─ "

            s_start = node.get("start_time_ns") or trace_start_ns
            offset_ms = round(max(0.0, (s_start - trace_start_ns) / 1_000_000.0), 2)
            dur_ms = round(float(node.get("duration_ms") or 0.0), 2)

            offset_pct = round(min(96.0, max(0.0, (offset_ms / trace_duration_ms) * 100.0)), 2)
            width_pct = round(min(100.0 - offset_pct, max(2.0, (dur_ms / trace_duration_ms) * 100.0)), 2)

            cur_node_id = node.get("node_id") or node.get("service_name") or ""
            crossed_network = bool(parent_node_id and cur_node_id and parent_node_id != cur_node_id)

            enriched = dict(node)
            enriched["index"] = len(ordered_spans) + 1
            enriched["depth"] = depth
            enriched["tree_prefix"] = prefix
            enriched["offset_ms"] = offset_ms
            enriched["offset_pct"] = offset_pct
            enriched["width_pct"] = width_pct
            enriched["crossed_network"] = crossed_network
            enriched["port_label"] = SERVICE_PORT_LABELS.get(cur_node_id, cur_node_id)
            ordered_spans.append(enriched)

            kids = children_map.get(sid or "", [])
            for idx, child in enumerate(kids):
                child_is_last = idx == (len(kids) - 1)
                _dfs(child, depth + 1, ancestor_pipes + [not is_last], child_is_last, cur_node_id)

        for r_idx, root in enumerate(roots):
            _dfs(root, 0, [], r_idx == (len(roots) - 1), None)

        # Catch any cycles/orphans
        for sp in spans_list:
            if sp.get("span_id") not in visited:
                _dfs(sp, 0, [], True, None)

        # Build human-readable Story summary
        services: List[str] = []
        for s in ordered_spans:
            nid = s.get("node_id") or s.get("service_name") or ""
            if nid and nid not in services:
                services.append(nid)

        span_names = [s.get("name") or "" for s in ordered_spans]
        all_attrs: Dict[str, Any] = {}
        for s in ordered_spans:
            all_attrs.update(s.get("attributes") or {})

        tick_val = next((s.get("tick") for s in ordered_spans if s.get("tick") is not None), all_attrs.get("cm.tick"))
        tick_suffix = f" on Tick {tick_val}" if tick_val is not None else ""
        model_name = all_attrs.get("gen_ai.request.model") or ""
        in_tok = all_attrs.get("gen_ai.usage.input_tokens")
        out_tok = all_attrs.get("gen_ai.usage.output_tokens")
        think_tok = all_attrs.get("gen_ai.usage.thinking_tokens") or all_attrs.get("gen_ai.usage.reasoning.output_tokens") or 0
        primary_agent = next((s.get("agent_id") for s in ordered_spans if s.get("agent_id")), "") or (services[0] if services else "service")

        category = "other"
        if any("coach" in n or "eval" in n for n in span_names) or all_attrs.get("gen_ai.role") == "coach":
            category = "coach"
            story = f"coach({primary_agent}) evaluates telemetry & updates strategy{tick_suffix}"
        elif any("actor_deliberation" in n or "tactician" in n or "agent.act" in n for n in span_names):
            category = "actor"
            story = f"{primary_agent} decides & executes a move{tick_suffix}"
            if model_name:
                story += f" [{model_name}]"
        elif any("sim.tick" in n for n in span_names):
            category = "tick"
            story = f"Game Server advances simulation clock{tick_suffix}"
        elif any("mcp." in n for n in span_names):
            category = "mcp"
            story = f"MCP Server tool / telemetry operation ({ordered_spans[0].get('name')})"
        else:
            story = f"{primary_agent} — {ordered_spans[0].get('name')}{tick_suffix}"

        has_error = any(s.get("status") == "ERROR" for s in ordered_spans)
        root_span = ordered_spans[0]

        traces_out.append(
            {
                "trace_id": tid,
                "story": story,
                "category": category,
                "root_span_name": root_span.get("name") or "root",
                "primary_node": services[0] if services else "",
                "services": services,
                "crossed_services": len(services) > 1,
                "span_count": len(ordered_spans),
                "tick": tick_val,
                "model": model_name,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "thinking_tokens": think_tok,
                "start_time_ns": trace_start_ns,
                "time_str": root_span.get("time_str") or "",
                "duration_ms": trace_duration_ms,
                "status": "ERROR" if has_error else "OK",
                "spans": ordered_spans,
            }
        )

    # Sort traces: multi-span / latest first
    traces_out.sort(key=lambda t: t.get("start_time_ns") or 0, reverse=True)
    return traces_out[:max_traces]


def build_exemplar_timeseries(
    node_snapshots: Dict[str, Any],
    trace_trees: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Builds time-series metric arrays with OpenTelemetry `trace_id` Exemplars linking spikes -> traces."""
    genai_points: List[Dict[str, Any]] = []
    tool_points: List[Dict[str, Any]] = []
    tick_points: List[Dict[str, Any]] = []

    # Index trace trees by (node_id, category) and by trace_id for fast exemplar correlation
    traces_by_id = {t["trace_id"]: t for t in trace_trees}
    traces_by_node_cat: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for t in trace_trees:
        key = (t.get("primary_node") or "", t.get("category") or "")
        traces_by_node_cat.setdefault(key, []).append(t)

    seen_trace_ids_in_genai = set()

    for nid, snap in node_snapshots.items():
        if not snap.get("online"):
            continue
        g = snap.get("genai") or {}
        for ev in g.get("recent_events") or []:
            pt = dict(ev)
            pt["node_id"] = nid
            tid = pt.get("trace_id")
            # If historical event lacks trace_id, correlate to closest trace by timestamp/role
            if not tid or tid not in traces_by_id:
                role = pt.get("role") or "actor"
                candidates = traces_by_node_cat.get((nid, role)) or traces_by_node_cat.get((nid, "actor")) or []
                best_t = None
                best_diff = 999999.0
                ev_ts = float(pt.get("ts") or 0.0)
                for cand in candidates:
                    cand_end_sec = ((cand.get("start_time_ns") or 0) / 1e9) + ((cand.get("duration_ms") or 0) / 1000.0)
                    diff = abs(ev_ts - cand_end_sec)
                    if diff < best_diff:
                        best_diff = diff
                        best_t = cand
                if best_t and best_diff <= 15.0:
                    pt["trace_id"] = best_t["trace_id"]
                    pt["has_live_trace"] = True
                elif best_t:
                    # Link to representative trace of same agent+role so clicking still drills into that agent's stack
                    pt["fallback_trace_id"] = best_t["trace_id"]
                    pt["has_live_trace"] = False
            else:
                pt["has_live_trace"] = True
            if pt.get("trace_id"):
                seen_trace_ids_in_genai.add(pt["trace_id"])
            genai_points.append(pt)

        t_info = snap.get("tools") or {}
        for tc in t_info.get("recent_calls") or []:
            tpt = dict(tc)
            tpt["node_id"] = nid
            tool_points.append(tpt)

        sim_info = snap.get("simulation") or {}
        for tk in sim_info.get("recent_ticks") or []:
            tkpt = dict(tk)
            tkpt["node_id"] = nid
            tick_points.append(tkpt)

    # Also ensure every live actor/coach trace in trace_trees has a clickable Exemplar point on the graph!
    for t in trace_trees:
        tid = t["trace_id"]
        if tid in seen_trace_ids_in_genai:
            continue
        if t.get("category") in ("actor", "coach"):
            start_sec = (t.get("start_time_ns") or 0) / 1e9
            in_tok = int(t.get("input_tokens") or 0)
            out_tok = int(t.get("output_tokens") or 0)
            think_tok = int(t.get("thinking_tokens") or 0)
            genai_points.append(
                {
                    "ts": start_sec + ((t.get("duration_ms") or 0) / 1000.0),
                    "time_str": t.get("time_str") or "",
                    "node_id": t.get("primary_node") or "agent",
                    "agent_id": t.get("primary_node") or "agent",
                    "role": t.get("category"),
                    "model": t.get("model") or "gemini-2.5-flash",
                    "tick": t.get("tick"),
                    "latency_ms": t.get("duration_ms") or 0.0,
                    "ttft_ms": round((t.get("duration_ms") or 0.0) * 0.7, 2),
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "thinking_tokens": think_tok,
                    "total_tokens": in_tok + out_tok + think_tok,
                    "status": t.get("status") or "OK",
                    "trace_id": tid,
                    "has_live_trace": True,
                }
            )

    genai_points.sort(key=lambda x: x.get("ts") or 0.0)
    tool_points.sort(key=lambda x: x.get("ts") or 0.0)
    tick_points.sort(key=lambda x: x.get("ts") or 0.0)

    return {
        "genai": genai_points[-140:],
        "tools": tool_points[-100:],
        "ticks": tick_points[-100:],
    }


async def fetch_cluster_sre_snapshot(local_node_id: Optional[str] = None) -> Dict[str, Any]:
    """Polls `/api/sre/status` from all cluster nodes and builds a unified SRE snapshot."""
    nodes = discover_cluster_nodes()
    local_store = get_sre_store()
    local_summary = local_store.get_summary(include_spans=True, span_limit=250)

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
            if (
                nid == local_node_id
                or (local_summary["agent_id"] and nid == local_summary["agent_id"])
                or (nid == "mcp-server" and local_summary["service_name"] == "cm-mcp-server")
                or (nid == "game-server" and local_summary["service_name"] == "cm-game-server")
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

    # Backfill any GenAI root spans or tool client spans that aged out of a node's fast-rolling span buffer
    known_span_ids = {sp.get("span_id") for sp in all_spans if sp.get("span_id")}
    for nid, snap in node_snapshots.items():
        if not snap.get("online"):
            continue
        svc_name = snap.get("service_name") or f"cm-{nid}"
        ag_id = snap.get("agent_id") or nid
        g = snap.get("genai") or {}
        for ev in g.get("recent_events") or []:
            tid = ev.get("trace_id")
            sid = ev.get("span_id")
            if not tid or not sid or sid in known_span_ids:
                continue
            ts_sec = float(ev.get("ts") or 0.0)
            dur_ms = float(ev.get("latency_ms") or 0.0)
            if ts_sec <= 0 or dur_ms <= 0:
                continue
            end_ns = int(ts_sec * 1_000_000_000)
            start_ns = end_ns - int(dur_ms * 1_000_000)
            role = ev.get("role") or "actor"
            sname = "genai.coach_eval" if role == "coach" else "genai.actor_deliberation"
            all_spans.append({
                "trace_id": tid,
                "span_id": sid,
                "parent_span_id": None,
                "name": sname,
                "kind": "CLIENT",
                "service_name": ev.get("service") or svc_name,
                "agent_id": ev.get("agent_id") or ag_id,
                "node_id": nid,
                "start_time_ns": start_ns,
                "end_time_ns": end_ns,
                "duration_ms": round(dur_ms, 2),
                "time_str": ev.get("time_str") or "",
                "status": "ERROR" if str(ev.get("status") or "OK").upper() not in ("OK", "SUCCESS") else "OK",
                "attributes": {
                    "gen_ai.system": "google_genai",
                    "gen_ai.role": role,
                    "gen_ai.request.model": ev.get("model") or "gemini-2.5-flash",
                    "gen_ai.usage.input_tokens": int(ev.get("input_tokens") or 0),
                    "gen_ai.usage.output_tokens": int(ev.get("output_tokens") or 0),
                    "gen_ai.usage.thinking_tokens": int(ev.get("thinking_tokens") or 0),
                    "gen_ai.response.ttft_ms": float(ev.get("ttft_ms") or 0.0),
                    "cm.tick": ev.get("tick"),
                },
            })
            known_span_ids.add(sid)

        t_info = snap.get("tools") or {}
        for tc in t_info.get("recent_calls") or []:
            tid = tc.get("trace_id")
            sid = tc.get("span_id")
            if not tid or not sid or sid in known_span_ids:
                continue
            ts_sec = float(tc.get("ts") or 0.0)
            dur_ms = float(tc.get("duration_ms") or 0.0)
            if ts_sec <= 0:
                continue
            end_ns = int(ts_sec * 1_000_000_000)
            start_ns = end_ns - int(dur_ms * 1_000_000)
            raw_tname = str(tc.get("tool_name") or "tool")
            if nid != "mcp-server" and not raw_tname.startswith("mcp.tool."):
                tname = f"mcp.tool.{raw_tname}"
            else:
                tname = raw_tname
            all_spans.append({
                "trace_id": tid,
                "span_id": sid,
                "parent_span_id": None,
                "name": tname,
                "kind": "CLIENT" if nid != "mcp-server" else "SERVER",
                "service_name": tc.get("service") or svc_name,
                "agent_id": tc.get("agent_id") or ag_id,
                "node_id": nid,
                "start_time_ns": start_ns,
                "end_time_ns": end_ns,
                "duration_ms": round(dur_ms, 2),
                "time_str": tc.get("time_str") or "",
                "status": "ERROR" if str(tc.get("status") or "ok").lower() not in ("ok", "success") else "OK",
                "attributes": {
                    "cm.tool.name": raw_tname,
                    "cm.tool.duration_ms": round(dur_ms, 2),
                    "cm.tool.cache_hit": bool(tc.get("cache_hit")),
                },
            })
            known_span_ids.add(sid)

    all_spans.sort(key=lambda x: x.get("start_time_ns") or 0, reverse=True)
    all_logs.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    trace_trees = build_trace_trees(all_spans, max_traces=80)
    timeseries = build_exemplar_timeseries(node_snapshots, trace_trees)

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
        "timeseries": timeseries,
        "traces": trace_trees,
        "spans": all_spans[:180],
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
      --indigo: #4f46e5;
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
      margin-right: 10px;
      padding: 5px 10px;
      border-radius: 6px;
      border: 1px solid transparent;
      font-weight: 500;
      font-size: 12px;
    }
    .nav-links a:hover, .nav-links a.active {
      color: var(--accent);
      border-color: #bae6fd;
      background: #f0f9ff;
    }
    .controls-bar {
      display: flex;
      align-items: center;
      gap: 10px;
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
    .toggle-btn.paused {
      border-color: #fde047;
      background: #fef9c3;
      color: #854d0e;
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
      max-width: 1700px;
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
      align-items: center;
    }
    .tab-btn {
      cursor: pointer;
      padding: 6px 12px;
      background: var(--panel);
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 6px;
      font-weight: 600;
      font-size: 12px;
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
      flex-wrap: wrap;
      gap: 8px;
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
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
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
    .badge.purple { background: #f3e8ff; color: #6d28d9; }
    .two-col {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 14px;
    }
    @media (max-width: 1100px) {
      .two-col { grid-template-columns: 1fr; }
    }

    /* --- Metric-to-Trace Exemplar Chart Styles --- */
    .exemplar-chart-wrap {
      position: relative;
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px 6px 12px;
    }
    .exemplar-legend {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      align-items: center;
      font-size: 11px;
      color: var(--muted);
    }
    .legend-item {
      display: flex;
      align-items: center;
      gap: 5px;
      font-weight: 600;
      color: var(--text);
    }
    .legend-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }
    .exemplar-dot {
      cursor: pointer;
      transition: r 0.12s ease, stroke-width 0.12s ease;
    }
    .exemplar-dot:hover {
      r: 7px !important;
      stroke: #0f172a !important;
      stroke-width: 2.5px !important;
    }
    .exemplar-tooltip {
      position: fixed;
      z-index: 9999;
      pointer-events: none;
      background: #0f172a;
      color: #f8fafc;
      padding: 8px 11px;
      border-radius: 6px;
      font-size: 11px;
      line-height: 1.45;
      box-shadow: 0 4px 12px rgba(15, 23, 42, 0.25);
      display: none;
      max-width: 340px;
    }
    .exemplar-banner {
      display: none;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      background: #eff6ff;
      border: 1px solid #93c5fd;
      border-left: 4px solid #2563eb;
      color: #1e3a8a;
      padding: 8px 12px;
      border-radius: 6px;
      margin-bottom: 10px;
      font-size: 12px;
    }

    /* --- Google Cloud Trace Waterfall Explorer Styles --- */
    .trace-toolbar {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 6px;
      margin-bottom: 12px;
    }
    .trace-story-row {
      cursor: pointer;
      transition: background 0.12s ease;
      border-left: 3px solid transparent;
    }
    .trace-story-row:hover {
      background: #f0f9ff;
    }
    .trace-story-row.expanded {
      background: #e0f2fe;
      border-left-color: #0284c7;
      font-weight: 600;
    }
    .trace-drilldown-cell {
      padding: 0 !important;
      background: #f8fafc;
      border-bottom: 2px solid #cbd5e1;
    }
    .trace-drilldown-wrap {
      padding: 14px 16px;
      display: grid;
      grid-template-columns: 1.45fr 1fr;
      gap: 16px;
      align-items: start;
    }
    @media (max-width: 1200px) {
      .trace-drilldown-wrap {
        grid-template-columns: 1fr;
      }
    }
    .ascii-story-box {
      background: #ffffff;
      border: 1px solid #cbd5e1;
      border-left: 4px solid #0284c7;
      border-radius: 6px;
      padding: 10px 14px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      color: #0f172a;
      margin-bottom: 12px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
    }
    .waterfall-table {
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
    }
    .waterfall-row {
      cursor: pointer;
      transition: background 0.1s ease;
    }
    .waterfall-row:hover {
      background: #f1f5f9;
    }
    .waterfall-row.selected {
      background: #eff6ff;
      outline: 1px solid #93c5fd;
    }
    .tree-cell {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      white-space: nowrap;
      font-size: 12px;
    }
    .tree-connector {
      color: #94a3b8;
      white-space: pre;
      user-select: none;
    }
    .gantt-track {
      position: relative;
      width: 100%;
      min-width: 240px;
      height: 18px;
      background: #f1f5f9;
      border-radius: 4px;
      overflow: hidden;
      border: 1px solid #e2e8f0;
    }
    .gantt-grid-line {
      position: absolute;
      top: 0;
      bottom: 0;
      width: 1px;
      background: #e2e8f0;
    }
    .gantt-bar {
      position: absolute;
      top: 2px;
      bottom: 2px;
      border-radius: 3px;
      min-width: 4px;
      display: flex;
      align-items: center;
      padding-left: 5px;
      font-size: 10px;
      font-weight: 700;
      color: #ffffff;
      overflow: visible;
      white-space: nowrap;
    }
    .gantt-bar.kind-genai { background: linear-gradient(90deg, #4f46e5, #7c3aed); }
    .gantt-bar.kind-actor { background: linear-gradient(90deg, #0284c7, #0ea5e9); }
    .gantt-bar.kind-tool { background: linear-gradient(90deg, #059669, #10b981); }
    .gantt-bar.kind-error { background: linear-gradient(90deg, #dc2626, #ef4444); }
    .gantt-ruler {
      display: flex;
      justify-content: space-between;
      font-size: 10px;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, monospace;
      padding: 0 2px;
    }
    .span-inspector-card {
      background: #ffffff;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 14px;
      position: sticky;
      top: 12px;
      box-shadow: 0 2px 6px rgba(15, 23, 42, 0.05);
    }
    .span-inspector-card h4 {
      margin: 0 0 8px 0;
      font-size: 13px;
      color: #0f172a;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 8px;
    }
    .attr-pre {
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 6px;
      padding: 8px 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px;
      max-height: 220px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      color: #1e293b;
      margin: 6px 0 10px 0;
    }
  </style>
</head>
<body>
  <div id="exemplar-tooltip" class="exemplar-tooltip"></div>

  <header>
    <div class="brand">
      <span>🔭 Cat & Mouse — OTel SRE Observability Mission Control</span>
      <nav class="nav-links">
        <a href="http://127.0.0.1:8000">🏟️ Arena (:8000)</a>
        <a href="http://127.0.0.1:8000/boxscore">📊 Boxscore</a>
        <a href="http://127.0.0.1:9050">🔌 MCP Hub (:9050)</a>
        <a href="/sre" class="active">📈 SRE Dashboard</a>
        <a href="/metrics" target="_blank">🔥 Prometheus /metrics</a>
        <a href="https://console.cloud.google.com/traces/list?project=catz-app-prod" target="_blank">☁️ GCP Cloud Trace</a>
      </nav>
    </div>
    <div class="controls-bar">
      <span><strong>Runtime Switches:</strong></span>
      <button id="btn-pause" class="toggle-btn on" onclick="togglePause()">🔄 Live Refresh: ON (2s)</button>
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
          <option value="gcp">Google Cloud Trace (catz-app-prod)</option>
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

    <!-- Metric-to-Trace Exemplar Time-Series Explorer (GCP Cloud Monitoring Style) -->
    <div class="panel">
      <h3>
        <span>📈 Metric-to-Trace Exemplar Time-Series Graphs — Click any spike/dot to drill into its Trace Call Stack!</span>
        <div style="display:flex; gap:6px; align-items:center;">
          <button class="tab-btn active" id="mchart-latency" onclick="setMetricChart('latency')">⚡ LLM Turn Latency (ms)</button>
          <button class="tab-btn" id="mchart-tokens" onclick="setMetricChart('tokens')">🔥 Token Burn per Call</button>
          <button class="tab-btn" id="mchart-tools" onclick="setMetricChart('tools')">🛠️ Tool & Sim Tick Latency (ms)</button>
        </div>
      </h3>

      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; flex-wrap:wrap; gap:8px;">
        <div class="exemplar-legend" id="exemplar-legend">
          <span class="legend-item"><span class="legend-dot" style="background:#0284c7"></span>cat-1 (Actor &amp; Coach)</span>
          <span class="legend-item"><span class="legend-dot" style="background:#16a34a"></span>cat-2 (Actor &amp; Coach)</span>
          <span class="legend-item"><span class="legend-dot" style="background:#dc2626"></span>cat-3 Evil (Actor &amp; Coach)</span>
          <span class="legend-item"><span style="display:inline-block; width:16px; border-top:2px dashed #475569;"></span>Coach Eval Series (Dashed)</span>
          <span class="legend-item"><span class="legend-dot" style="background:#ffffff; border:2.5px solid #f59e0b"></span>P90+ Spike Halo (Click Dot ➔ Open Trace)</span>
        </div>
        <span style="font-size:11px; color:var(--muted);" id="exemplar-points-badge">0 exemplar measurements</span>
      </div>

      <div class="exemplar-chart-wrap" id="exemplar-chart-container"></div>
    </div>

    <!-- Google Cloud Trace Waterfall Explorer (Full Width Centerpiece) -->
    <div class="panel" id="trace-explorer-panel">
      <h3>
        <span>🌲 Distributed Trace Explorer — Google Cloud Trace Waterfall (`TRACE ID` ➔ Call Stack Tree ➔ Span Inspector)</span>
        <span class="badge info" id="trace-count-badge">0 traces</span>
      </h3>

      <div id="exemplar-active-banner" class="exemplar-banner"></div>

      <div class="trace-toolbar">
        <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
          <strong>Category:</strong>
          <button class="tab-btn active" id="tcat-all" onclick="setTraceCategory('all')">All Stories</button>
          <button class="tab-btn" id="tcat-multi" onclick="setTraceCategory('multi')">Multi-Span Stacks (2+)</button>
          <button class="tab-btn" id="tcat-actor" onclick="setTraceCategory('actor')">🐱 Actor Deliberations</button>
          <button class="tab-btn" id="tcat-coach" onclick="setTraceCategory('coach')">🧠 Coach Evaluations</button>
          <button class="tab-btn" id="tcat-mcp" onclick="setTraceCategory('mcp')">🔌 MCP / Cross-Service</button>
          <button class="tab-btn" id="tcat-tick" onclick="setTraceCategory('tick')">⏱️ Game Ticks</button>
          <button class="tab-btn" id="tcat-err" onclick="setTraceCategory('err')">🚨 Errors Only</button>
        </div>
        <div style="display:flex; gap:8px; align-items:center;">
          <input type="text" id="trace-search" placeholder="Filter by trace_id, span, model, tick…" oninput="renderTraceExplorer()" style="width:230px;" />
          <span style="color:var(--muted); font-size:11px;">Click any Trace row to expand its Call Stack & Gantt Waterfall</span>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th style="width:28px;"></th>
            <th>Time (UTC)</th>
            <th>Trace ID</th>
            <th>Request Story & Root Trigger</th>
            <th>Services Involved</th>
            <th>Spans</th>
            <th>Tokens (In / Out)</th>
            <th>Total Duration</th>
          </tr>
        </thead>
        <tbody id="traces-tbody"></tbody>
      </table>
    </div>

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

    <!-- Correlated Logs -->
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
            <th>Trace ID (Click to Jump)</th>
          </tr>
        </thead>
        <tbody id="logs-tbody"></tbody>
      </table>
    </div>
  </main>

  <script>
    let currentFilter = 'all';
    let currentTraceCategory = 'all';
    let currentMetricChart = 'latency';
    let latestData = null;
    let isPaused = false;
    let expandedTraceIds = new Set();
    let selectedSpanByTrace = {}; // trace_id -> span_id
    let autoExpandedOnce = false;
    let selectedExemplarPoint = null;
    let lastRenderedExemplarPoints = [];

    function escapeHtml(str) {
      return String(str == null ? '' : str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function togglePause() {
      isPaused = !isPaused;
      const btn = document.getElementById('btn-pause');
      btn.textContent = isPaused ? '⏸ Live Refresh: PAUSED' : '🔄 Live Refresh: ON (2s)';
      btn.className = `toggle-btn ${isPaused ? 'paused' : 'on'}`;
    }

    function selectNode(nodeId) {
      currentFilter = nodeId;
      renderDashboard();
    }

    function setMetricChart(mode) {
      currentMetricChart = mode;
      for (const m of ['latency', 'tokens', 'tools']) {
        const el = document.getElementById(`mchart-${m}`);
        if (el) el.className = `tab-btn ${m === mode ? 'active' : ''}`;
      }
      renderExemplarChart();
    }

    function setTraceCategory(cat) {
      currentTraceCategory = cat;
      for (const id of ['all', 'multi', 'actor', 'coach', 'mcp', 'tick', 'err']) {
        const el = document.getElementById(`tcat-${id}`);
        if (el) el.className = `tab-btn ${id === cat ? 'active' : ''}`;
      }
      renderTraceExplorer();
    }

    function clearExemplarSelection() {
      selectedExemplarPoint = null;
      const banner = document.getElementById('exemplar-active-banner');
      if (banner) banner.style.display = 'none';
      document.getElementById('trace-search').value = '';
      currentTraceCategory = 'all';
      for (const id of ['all', 'multi', 'actor', 'coach', 'mcp', 'tick', 'err']) {
        const el = document.getElementById(`tcat-${id}`);
        if (el) el.className = `tab-btn ${id === 'all' ? 'active' : ''}`;
      }
      renderExemplarChart();
      renderTraceExplorer();
    }

    function onExemplarDotClick(idx) {
      const pt = lastRenderedExemplarPoints[idx];
      if (!pt) return;
      selectedExemplarPoint = pt;

      const tid = pt.trace_id || pt.fallback_trace_id || '';
      const allTraces = (latestData && latestData.traces) || [];
      const exactMatch = tid ? allTraces.find(t => t.trace_id === tid) : null;

      const banner = document.getElementById('exemplar-active-banner');
      if (banner) {
        banner.style.display = 'flex';
        const metricLabel = currentMetricChart === 'tokens'
          ? `${(pt.total_tokens || 0).toLocaleString()} tokens (In: ${(pt.input_tokens || 0).toLocaleString()}, Out: ${(pt.output_tokens || 0).toLocaleString()}, Think: ${(pt.thinking_tokens || 0).toLocaleString()})`
          : `${pt.val} ms`;
        banner.innerHTML = `
          <div>
            <strong>🎯 Metric Exemplar Selected:</strong>
            <span class="badge info" style="margin:0 4px;">${escapeHtml(pt.seriesLabel)}</span>
            at <code>${escapeHtml(pt.time_str || '')}</code> —
            <strong>${metricLabel}</strong>
            ${exactMatch
              ? ` ➔ Linked directly to <code>trace_id=${escapeHtml(tid.slice(0, 12))}…</code> (expanded below!)`
              : ` ➔ Showing matching traces for <strong>${escapeHtml(pt.node_id || '')} (${escapeHtml(pt.role || 'all')})</strong>`
            }
          </div>
          <button class="toggle-btn off" onclick="clearExemplarSelection()">✕ Clear Exemplar Filter</button>
        `;
      }

      if (exactMatch) {
        expandedTraceIds.add(exactMatch.trace_id);
        currentTraceCategory = 'all';
        document.getElementById('trace-search').value = exactMatch.trace_id.slice(0, 12);
      } else {
        // Filter to the agent & role of that spike and expand its top trace
        document.getElementById('trace-search').value = pt.node_id || '';
        const roleCat = (pt.role === 'coach' || pt.role === 'actor') ? pt.role : 'all';
        setTraceCategory(roleCat);
        const bestTrace = allTraces.find(t => (t.primary_node === pt.node_id || (t.services || []).includes(pt.node_id)) && (roleCat === 'all' || t.category === roleCat));
        if (bestTrace) {
          expandedTraceIds.add(bestTrace.trace_id);
        }
      }

      renderExemplarChart();
      renderTraceExplorer();
      document.getElementById('trace-explorer-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function showExemplarTooltip(ev, idx) {
      const pt = lastRenderedExemplarPoints[idx];
      const tip = document.getElementById('exemplar-tooltip');
      if (!pt || !tip) return;
      const tid = pt.trace_id || pt.fallback_trace_id || '';
      tip.style.display = 'block';
      tip.style.left = `${Math.min(window.innerWidth - 350, ev.clientX + 14)}px`;
      tip.style.top = `${Math.max(12, ev.clientY - 70)}px`;
      tip.innerHTML = `
        <div style="font-weight:700; color:#38bdf8; margin-bottom:2px;">${escapeHtml(pt.seriesLabel)} — ${escapeHtml(pt.time_str || '')}</div>
        <div><strong>Value:</strong> ${escapeHtml(pt.displayVal)}</div>
        ${pt.total_tokens ? `<div><strong>Tokens:</strong> ${pt.total_tokens.toLocaleString()} (In: ${pt.input_tokens || 0}, Out: ${pt.output_tokens || 0}, Think: ${pt.thinking_tokens || 0})</div>` : ''}
        ${tid ? `<div><strong>Exemplar Trace:</strong> <code>${escapeHtml(tid.slice(0, 14))}…</code></div>` : ''}
        <div style="color:#fde047; margin-top:3px; font-weight:600;">🖱️ Click dot to drill into Trace Call Stack</div>
      `;
    }

    function hideExemplarTooltip() {
      const tip = document.getElementById('exemplar-tooltip');
      if (tip) tip.style.display = 'none';
    }

    function seriesColor(nodeId, role) {
      const id = String(nodeId || '').toLowerCase();
      if (id.includes('cat-1') || id.includes('cat_1')) return '#0284c7';
      if (id.includes('cat-2') || id.includes('cat_2')) return '#16a34a';
      if (id.includes('cat-3') || id.includes('cat_3')) return '#dc2626';
      if (id === 'game-server') return '#0ea5e9';
      if (id === 'mcp-server') return '#059669';
      if (role === 'coach') return '#7c3aed';
      return '#4f46e5';
    }

    function renderExemplarChart() {
      const container = document.getElementById('exemplar-chart-container');
      if (!container || !latestData) return;

      const ts = latestData.timeseries || {};
      let rawPoints = [];
      let yUnit = 'ms';

      if (currentMetricChart === 'latency') {
        rawPoints = (ts.genai || [])
          .filter(p => currentFilter === 'all' || p.node_id === currentFilter || p.agent_id === currentFilter)
          .map(p => ({
            ...p,
            val: Number(p.latency_ms || 0),
            displayVal: `${Number(p.latency_ms || 0).toLocaleString()} ms (TTFT: ${p.ttft_ms || 0} ms)`,
            seriesKey: `${p.node_id}:${p.role}`,
            seriesLabel: `${p.node_id} (${p.role})`,
            color: seriesColor(p.node_id || p.agent_id, p.role),
          }));
        yUnit = 'ms';
      } else if (currentMetricChart === 'tokens') {
        rawPoints = (ts.genai || [])
          .filter(p => currentFilter === 'all' || p.node_id === currentFilter || p.agent_id === currentFilter)
          .map(p => ({
            ...p,
            val: Number(p.total_tokens || 0),
            displayVal: `${Number(p.total_tokens || 0).toLocaleString()} tokens`,
            seriesKey: `${p.node_id}:${p.role}`,
            seriesLabel: `${p.node_id} (${p.role})`,
            color: seriesColor(p.node_id || p.agent_id, p.role),
          }));
        yUnit = 'tok';
      } else {
        const toolPts = (ts.tools || [])
          .filter(p => currentFilter === 'all' || p.node_id === currentFilter || p.agent_id === currentFilter)
          .map(p => ({
            ...p,
            val: Number(p.duration_ms || 0),
            displayVal: `${p.tool_name}: ${p.duration_ms || 0} ms`,
            seriesKey: `tool:${p.node_id}`,
            seriesLabel: `${p.node_id} tool (${p.tool_name})`,
            color: seriesColor(p.node_id || p.agent_id, 'tool'),
          }));
        const tickPts = (ts.ticks || [])
          .filter(p => currentFilter === 'all' || currentFilter === 'game-server')
          .map(p => ({
            ...p,
            val: Number(p.duration_ms || 0),
            displayVal: `Tick #${p.tick}: ${p.duration_ms || 0} ms`,
            seriesKey: 'game-server:tick',
            seriesLabel: `Game Tick #${p.tick}`,
            color: '#0284c7',
          }));
        rawPoints = [...toolPts, ...tickPts].sort((a, b) => (a.ts || 0) - (b.ts || 0));
        yUnit = 'ms';
      }

      lastRenderedExemplarPoints = rawPoints;
      document.getElementById('exemplar-points-badge').textContent = `${rawPoints.length} clickable exemplar dots`;

      if (rawPoints.length === 0) {
        container.innerHTML = `<div style="padding:28px; text-align:center; color:var(--muted);">No metric points in current filter yet.</div>`;
        return;
      }

      const W = 1200;
      const H = 225;
      const padL = 64;
      const padR = 24;
      const padT = 18;
      const padB = 28;
      const plotW = W - padL - padR;
      const plotH = H - padT - padB;

      const vals = rawPoints.map(p => p.val);
      const sortedVals = [...vals].sort((a, b) => a - b);
      const maxVal = Math.max(10, sortedVals[sortedVals.length - 1] * 1.08);
      const p90Val = sortedVals[Math.floor(sortedVals.length * 0.88)] || maxVal;

      // Position points evenly across X in chronological order so bursts don't overlap on the same pixel
      const n = rawPoints.length;
      const coords = rawPoints.map((pt, i) => {
        const x = padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
        const y = padT + plotH - Math.min(plotH, Math.max(0, (pt.val / maxVal) * plotH));
        return { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10, pt, i };
      });

      // Group polylines by seriesKey
      const bySeries = {};
      for (const c of coords) {
        if (!bySeries[c.pt.seriesKey]) {
          bySeries[c.pt.seriesKey] = { color: c.pt.color, role: c.pt.role, pts: [] };
        }
        bySeries[c.pt.seriesKey].pts.push(`${c.x},${c.y}`);
      }

      let gridSvg = '';
      for (const frac of [0, 0.25, 0.5, 0.75, 1.0]) {
        const gy = Math.round(padT + plotH * (1 - frac));
        const labelVal = Math.round(maxVal * frac).toLocaleString();
        gridSvg += `
          <line x1="${padL}" y1="${gy}" x2="${W - padR}" y2="${gy}" stroke="#e2e8f0" stroke-dasharray="${frac === 0 ? '' : '3,3'}" />
          <text x="${padL - 8}" y="${gy + 4}" text-anchor="end" fill="#64748b" font-size="10">${labelVal} ${yUnit}</text>
        `;
      }

      // P90 Spike Reference Line
      const p90Y = Math.round(padT + plotH - (p90Val / maxVal) * plotH);
      gridSvg += `
        <line x1="${padL}" y1="${p90Y}" x2="${W - padR}" y2="${p90Y}" stroke="#f59e0b" stroke-width="1" stroke-dasharray="5,4" />
        <text x="${W - padR - 4}" y="${Math.max(12, p90Y - 4)}" text-anchor="end" fill="#d97706" font-size="10" font-weight="600">P90 Spike Threshold (${Math.round(p90Val).toLocaleString()} ${yUnit})</text>
      `;

      let linesSvg = '';
      for (const s of Object.values(bySeries)) {
        if (s.pts.length > 1) {
          const dashAttr = s.role === 'coach' ? ' stroke-dasharray="5,3"' : '';
          linesSvg += `<polyline fill="none" stroke="${s.color}" stroke-width="1.75" stroke-opacity="0.7"${dashAttr} points="${s.pts.join(' ')}" />`;
        }
      }

      let dotsSvg = '';
      for (const c of coords) {
        const isSpike = c.pt.val >= p90Val && n > 4;
        const isSelected = selectedExemplarPoint && selectedExemplarPoint.ts === c.pt.ts && selectedExemplarPoint.node_id === c.pt.node_id;
        const r = isSelected ? 7 : (isSpike ? 5.2 : 4);
        if (isSpike || isSelected) {
          dotsSvg += `<circle cx="${c.x}" cy="${c.y}" r="${r + 3.5}" fill="none" stroke="${isSelected ? '#2563eb' : '#f59e0b'}" stroke-width="${isSelected ? 2.5 : 1.5}" stroke-opacity="0.85" />`;
        }
        dotsSvg += `
          <circle
            class="exemplar-dot"
            cx="${c.x}"
            cy="${c.y}"
            r="${r}"
            fill="${c.pt.color}"
            stroke="#ffffff"
            stroke-width="1.5"
            onmousemove="showExemplarTooltip(event, ${c.i})"
            onmouseleave="hideExemplarTooltip()"
            onclick="onExemplarDotClick(${c.i})"
          />
        `;
      }

      // Time axis labels (first, 25%, 50%, 75%, last)
      let xLabelsSvg = '';
      for (const frac of [0, 0.25, 0.5, 0.75, 1.0]) {
        const idx = Math.min(n - 1, Math.floor(frac * (n - 1)));
        const item = coords[idx];
        if (item) {
          xLabelsSvg += `<text x="${item.x}" y="${H - 6}" text-anchor="middle" fill="#64748b" font-size="10">${escapeHtml(item.pt.time_str || '')}</text>`;
        }
      }

      container.innerHTML = `
        <svg viewBox="0 0 ${W} ${H}" style="width:100%; height:225px; display:block; overflow:visible;">
          ${gridSvg}
          ${linesSvg}
          ${dotsSvg}
          ${xLabelsSvg}
        </svg>
      `;
    }

    function toggleTraceExpand(traceId) {
      if (expandedTraceIds.has(traceId)) {
        expandedTraceIds.delete(traceId);
      } else {
        expandedTraceIds.add(traceId);
      }
      renderTraceExplorer();
    }

    function selectSpanInTrace(traceId, spanId, ev) {
      if (ev) ev.stopPropagation();
      selectedSpanByTrace[traceId] = spanId;
      renderTraceExplorer();
    }

    function jumpToTrace(traceId) {
      if (!traceId) return;
      expandedTraceIds.add(traceId);
      currentTraceCategory = 'all';
      document.getElementById('trace-search').value = traceId.slice(0, 12);
      renderTraceExplorer();
      document.getElementById('traces-tbody').scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function spanBarClass(span) {
      if (span.status === 'ERROR') return 'kind-error';
      const n = (span.name || '').toLowerCase();
      if (n.includes('generate_content') || n.includes('call_llm') || n.includes('genai')) return 'kind-genai';
      if (n.includes('tool') || n.includes('mcp')) return 'kind-tool';
      return 'kind-actor';
    }

    function buildAsciiTraceBlock(trace) {
      const lines = [];
      lines.push(`TRACE ID: ${trace.trace_id} (The Story: "${trace.story}")`);
      lines.push('│');
      for (const s of (trace.spans || [])) {
        const netNote = s.crossed_network ? '  <-- Crossed network, same trace_id!' : '';
        const errNote = s.status === 'ERROR' ? '  [ERROR]' : '';
        lines.push(`${s.tree_prefix}[Span ${s.index}: ${s.port_label || s.node_id}] ${s.name} (${s.duration_ms}ms)${netNote}${errNote}`);
      }
      return lines.join('\\n');
    }

    function renderSpanInspector(trace, span) {
      if (!span) {
        return `<div class="span-inspector-card"><p style="color:var(--muted)">Select a span from the waterfall tree to inspect its attributes.</p></div>`;
      }
      const attrs = span.attributes || {};
      const llmReq = attrs['gcp.vertex.agent.llm_request'] || attrs['gen_ai.prompt'] || '';
      const llmResp = attrs['gcp.vertex.agent.llm_response'] || attrs['gen_ai.completion'] || '';
      const inTok = attrs['gen_ai.usage.input_tokens'];
      const outTok = attrs['gen_ai.usage.output_tokens'];
      const thinkTok = attrs['gen_ai.usage.thinking_tokens'] || attrs['gen_ai.usage.reasoning.output_tokens'];
      const cacheTok = attrs['gen_ai.usage.cache_read.input_tokens'];

      let attrRows = '';
      for (const [k, v] of Object.entries(attrs)) {
        if (k === 'gcp.vertex.agent.llm_request' || k === 'gcp.vertex.agent.llm_response') continue;
        attrRows += `<tr><th style="width:42%; word-break:break-all;"><code>${escapeHtml(k)}</code></th><td style="word-break:break-word;">${escapeHtml(v)}</td></tr>`;
      }

      return `
        <div class="span-inspector-card" onclick="event.stopPropagation()">
          <h4>
            <span>🔍 Span #${span.index}: <code>${escapeHtml(span.name)}</code></span>
            <span class="badge ${span.status === 'ERROR' ? 'err' : 'ok'}">${escapeHtml(span.status)} (${span.duration_ms} ms)</span>
          </h4>
          <table style="margin-bottom:10px;">
            <tbody>
              <tr><th>Service / Process</th><td><span class="badge info">${escapeHtml(span.port_label || span.service_name)}</span></td><th>Start Offset</th><td><code>+${span.offset_ms} ms</code> (${escapeHtml(span.time_str)})</td></tr>
              <tr><th>Span ID</th><td><code>${escapeHtml(span.span_id)}</code></td><th>Parent Span ID</th><td><code>${escapeHtml(span.parent_span_id || 'ROOT (None)')}</code></td></tr>
              <tr><th>Span Kind</th><td><code>${escapeHtml(span.kind)}</code></td><th>Cross-Process?</th><td>${span.crossed_network ? '<span class="badge purple">🌐 Yes — W3C traceparent</span>' : 'Same process'}</td></tr>
              ${(inTok != null || outTok != null) ? `
                <tr>
                  <th>Token Usage</th>
                  <td colspan="3">
                    <span class="badge info">In: ${inTok || 0}</span>
                    <span class="badge ok">Out: ${outTok || 0}</span>
                    ${thinkTok != null ? `<span class="badge purple">Think: ${thinkTok}</span>` : ''}
                    ${cacheTok != null ? `<span class="badge warn">Cached: ${cacheTok}</span>` : ''}
                  </td>
                </tr>
              ` : ''}
            </tbody>
          </table>

          ${llmResp ? `
            <details open>
              <summary style="cursor:pointer; font-weight:600; color:var(--indigo); margin-bottom:4px;">💬 LLM Response / Completion Preview</summary>
              <div class="attr-pre">${escapeHtml(llmResp)}</div>
            </details>
          ` : ''}

          ${llmReq ? `
            <details>
              <summary style="cursor:pointer; font-weight:600; color:var(--accent); margin-bottom:4px;">📝 LLM Prompt / Request Payload (${llmReq.length.toLocaleString()} chars)</summary>
              <div class="attr-pre">${escapeHtml(llmReq)}</div>
            </details>
          ` : ''}

          <div style="font-weight:600; margin:8px 0 4px 0; font-size:12px;">OpenTelemetry Span Attributes</div>
          <div style="max-height:240px; overflow:auto; border:1px solid var(--border); border-radius:6px;">
            <table>
              <tbody>
                ${attrRows || '<tr><td style="color:var(--muted)">No additional attributes on this span</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderTraceExplorer() {
      if (!latestData) return;
      const allTraces = latestData.traces || [];
      const searchQ = (document.getElementById('trace-search')?.value || '').trim().toLowerCase();

      const filtered = allTraces.filter(t => {
        if (currentFilter !== 'all' && !(t.services || []).includes(currentFilter) && t.primary_node !== currentFilter) {
          return false;
        }
        if (currentTraceCategory === 'multi' && (t.span_count || 0) < 2) return false;
        if (currentTraceCategory === 'actor' && t.category !== 'actor') return false;
        if (currentTraceCategory === 'coach' && t.category !== 'coach') return false;
        if (currentTraceCategory === 'mcp' && t.category !== 'mcp' && !t.crossed_services) return false;
        if (currentTraceCategory === 'tick' && t.category !== 'tick') return false;
        if (currentTraceCategory === 'err' && t.status !== 'ERROR') return false;

        if (searchQ) {
          const hay = `${t.trace_id} ${t.story} ${t.primary_node || ''} ${(t.services || []).join(' ')} ${t.model || ''} ${t.tick != null ? t.tick : ''} ${(t.spans || []).map(s => s.name).join(' ')}`.toLowerCase();
          if (!hay.includes(searchQ)) return false;
        }
        return true;
      });

      document.getElementById('trace-count-badge').textContent = `${filtered.length} trace stories`;

      // Auto-expand the most recent multi-span trace on initial load so the user sees the tree immediately
      if (!autoExpandedOnce && filtered.length > 0) {
        const firstMulti = filtered.find(t => (t.span_count || 0) >= 2) || filtered[0];
        if (firstMulti) {
          expandedTraceIds.add(firstMulti.trace_id);
          autoExpandedOnce = true;
        }
      }

      let html = '';
      for (const t of filtered.slice(0, 45)) {
        const isExp = expandedTraceIds.has(t.trace_id);
        const caret = isExp ? '▼' : '▶';
        const statusBadge = t.status === 'ERROR' ? 'err' : 'ok';
        const svcBadges = (t.services || []).map(s => `<span class="badge ${s === 'mcp-server' ? 'purple' : 'info'}" style="margin-right:4px;">${escapeHtml(s)}</span>`).join('');
        const tokText = (t.input_tokens != null || t.output_tokens != null)
          ? `${(t.input_tokens || 0).toLocaleString()} / ${(t.output_tokens || 0).toLocaleString()}`
          : '-';

        html += `
          <tr class="trace-story-row ${isExp ? 'expanded' : ''}" onclick="toggleTraceExpand('${t.trace_id}')">
            <td style="font-weight:700; color:var(--accent);">${caret}</td>
            <td>${escapeHtml(t.time_str)}</td>
            <td><code>${escapeHtml(t.trace_id.slice(0, 12))}…</code></td>
            <td>
              <strong>${escapeHtml(t.story)}</strong>
              ${t.crossed_services ? '<span class="badge purple" style="margin-left:6px;">🌐 Cross-Service</span>' : ''}
            </td>
            <td>${svcBadges}</td>
            <td><span class="badge ${t.span_count > 1 ? 'purple' : 'info'}">${t.span_count} span${t.span_count === 1 ? '' : 's'}</span></td>
            <td>${tokText}</td>
            <td><span class="badge ${statusBadge}">${t.duration_ms} ms</span></td>
          </tr>
        `;

        if (isExp) {
          const spans = t.spans || [];
          const selectedSpanId = selectedSpanByTrace[t.trace_id] || (spans.find(s => (s.name || '').includes('call_llm') || (s.name || '').includes('deliberation')) || spans[0] || {}).span_id;
          const selectedSpan = spans.find(s => s.span_id === selectedSpanId) || spans[0];
          const asciiBlock = buildAsciiTraceBlock(t);
          const totalDur = Math.max(0.01, t.duration_ms || 1);

          let wfRows = '';
          for (const sp of spans) {
            const isSel = selectedSpan && sp.span_id === selectedSpan.span_id;
            const barCls = spanBarClass(sp);
            wfRows += `
              <tr class="waterfall-row ${isSel ? 'selected' : ''}" onclick="selectSpanInTrace('${t.trace_id}', '${sp.span_id}', event)">
                <td class="tree-cell">
                  <span class="tree-connector">${escapeHtml(sp.tree_prefix)}</span><span class="badge info" style="font-size:10px; margin-right:4px;">Span ${sp.index}: ${escapeHtml(sp.node_id || sp.service_name)}</span><code>${escapeHtml(sp.name)}</code>
                  ${sp.crossed_network ? '<span class="badge purple" style="margin-left:4px;">🌐 traceparent</span>' : ''}
                </td>
                <td style="width:85px; text-align:right; font-family:ui-monospace,monospace;">${sp.duration_ms} ms</td>
                <td style="width:42%;">
                  <div class="gantt-track">
                    <div class="gantt-grid-line" style="left:25%"></div>
                    <div class="gantt-grid-line" style="left:50%"></div>
                    <div class="gantt-grid-line" style="left:75%"></div>
                    <div class="gantt-bar ${barCls}" style="left:${sp.offset_pct}%; width:${sp.width_pct}%;">
                      +${sp.offset_ms}ms
                    </div>
                  </div>
                </td>
              </tr>
            `;
          }

          html += `
            <tr>
              <td colspan="8" class="trace-drilldown-cell">
                <div class="trace-drilldown-wrap">
                  <div>
                    <div class="ascii-story-box">${escapeHtml(asciiBlock)}</div>
                    <div class="waterfall-table">
                      <table>
                        <thead>
                          <tr>
                            <th>Call Stack Hierarchy (Click any Span to inspect attributes & prompts)</th>
                            <th style="text-align:right;">Duration</th>
                            <th>
                              <div class="gantt-ruler">
                                <span>0 ms</span>
                                <span>${Math.round(totalDur * 0.25)} ms</span>
                                <span>${Math.round(totalDur * 0.5)} ms</span>
                                <span>${Math.round(totalDur * 0.75)} ms</span>
                                <span>${totalDur} ms</span>
                              </div>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          ${wfRows}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div>
                    ${renderSpanInspector(t, selectedSpan)}
                  </div>
                </div>
              </td>
            </tr>
          `;
        }
      }

      document.getElementById('traces-tbody').innerHTML = html || '<tr><td colspan="8" style="color:var(--muted)">No matching traces recorded yet. Start or step the simulation to generate traces.</td></tr>';
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
      await pollSRE(true);
    }

    async function changeLogLevel(level) {
      await fetch('/api/sre/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ console_log_level: level, broadcast: true }),
      });
      await pollSRE(true);
    }

    async function changeBackend(backend) {
      await fetch('/api/sre/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ backend: backend, broadcast: true }),
      });
      await pollSRE(true);
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

      // Render Metric-to-Trace Exemplar Graph & Trace Waterfall Explorer
      renderExemplarChart();
      renderTraceExplorer();

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

      // Logs Table
      const logs = (latestData.logs || []).filter(l => currentFilter === 'all' || l.node_id === currentFilter || l.agent_id === currentFilter);
      document.getElementById('log-count-badge').textContent = `${logs.length} logs`;
      let logsHtml = '';
      for (const l of logs.slice(0, 35)) {
        const b = l.level === 'ERROR' ? 'err' : (l.level === 'WARNING' ? 'warn' : 'ok');
        const tid = l.trace_id || '';
        logsHtml += `
          <tr>
            <td>${l.time_str || ''}</td>
            <td><strong>${l.node_id || l.service}</strong></td>
            <td><span class="badge ${b}">${l.level}</span></td>
            <td>${escapeHtml(l.message || '')}</td>
            <td>${tid ? `<a href="javascript:void(0)" onclick="jumpToTrace('${tid}')"><code>${tid.slice(0, 12)}…</code></a>` : '-'}</td>
          </tr>
        `;
      }
      document.getElementById('logs-tbody').innerHTML = logsHtml || '<tr><td colspan="5" style="color:var(--muted)">No correlated logs recorded yet</td></tr>';
    }

    async function pollSRE(force = false) {
      if (isPaused && !force) return;
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

    pollSRE(true);
    setInterval(() => pollSRE(false), 2000);
  </script>
</body>
</html>
"""
