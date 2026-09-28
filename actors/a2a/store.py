"""Storage and resolution for Agent-to-Agent (A2A) messages and Agent Cards."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from actors.a2a.models import A2AMessage, AgentCard, AgentSkill
from actors.cat.store import _resolve_runtime_dir


def normalize_agent_id(agent_id: Optional[str]) -> str:
    """Normalizes agent ID strings such as 'coach(cat-1)', 'coach_cat-1', 'cat-1-coach', 'actor(cat-1)' to 'cat-1'."""
    if not agent_id:
        return "cat-1"
    clean = (
        str(agent_id)
        .lower()
        .replace("coach(", "")
        .replace(")", "")
        .replace("coach_", "")
        .replace("-coach", "")
        .replace("actor(", "")
        .strip()
    )
    return clean or "cat-1"


def get_agent_runtime_dir(agent_id: Optional[str] = None) -> Path:
    """Returns the dedicated runtime directory for an agent."""
    return _resolve_runtime_dir(normalize_agent_id(agent_id))


def get_inbox_path(agent_id: Optional[str] = None) -> Path:
    d = get_agent_runtime_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / "a2a_inbox.jsonl"


def get_outbox_path(agent_id: Optional[str] = None) -> Path:
    d = get_agent_runtime_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / "a2a_outbox.jsonl"


# In-memory rolling cache per agent (keeps up to 100 recent messages)
_INBOX_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_OUTBOX_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def append_inbox(agent_id: str, message: A2AMessage) -> None:
    """Appends an incoming A2A message to the agent's inbox."""
    payload = message.model_dump()
    path = get_inbox_path(agent_id)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception as exc:
        print(f"[A2A Store Error] Failed to write inbox: {exc}", flush=True)

    cache = _INBOX_CACHE.setdefault(agent_id, [])
    cache.append(payload)
    if len(cache) > 100:
        cache.pop(0)


def append_outbox(agent_id: str, message: A2AMessage) -> None:
    """Appends an outgoing A2A message to the agent's outbox."""
    payload = message.model_dump()
    path = get_outbox_path(agent_id)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception as exc:
        print(f"[A2A Store Error] Failed to write outbox: {exc}", flush=True)

    cache = _OUTBOX_CACHE.setdefault(agent_id, [])
    cache.append(payload)
    if len(cache) > 100:
        cache.pop(0)


def get_inbox(agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns the most recent incoming A2A messages for an agent."""
    if agent_id in _INBOX_CACHE and _INBOX_CACHE[agent_id]:
        return _INBOX_CACHE[agent_id][-limit:]

    path = get_inbox_path(agent_id)
    if not path.exists():
        return []

    messages = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        return []

    _INBOX_CACHE[agent_id] = messages[-100:]
    return messages[-limit:]


def get_outbox(agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns the most recent outgoing A2A messages dispatched by an agent."""
    if agent_id in _OUTBOX_CACHE and _OUTBOX_CACHE[agent_id]:
        return _OUTBOX_CACHE[agent_id][-limit:]

    path = get_outbox_path(agent_id)
    if not path.exists():
        return []

    messages = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        return []

    _OUTBOX_CACHE[agent_id] = messages[-100:]
    return messages[-limit:]


def clear_a2a_history(agent_id: Optional[str] = None) -> None:
    """Clears both inbox and outbox for an agent, or all agents (used during test setup or game resets)."""
    if not agent_id or agent_id in ("all", "*"):
        _INBOX_CACHE.clear()
        _OUTBOX_CACHE.clear()
        from actors.cat.store import list_known_cat_ids, LOCAL_RUNTIME_DIR
        agent_ids = list_known_cat_ids()
        targets = [
            LOCAL_RUNTIME_DIR / "a2a_inbox.jsonl",
            LOCAL_RUNTIME_DIR / "a2a_outbox.jsonl",
        ]
        for aid in agent_ids:
            targets.extend([get_inbox_path(aid), get_outbox_path(aid)])
        for p in targets:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
    else:
        norm = normalize_agent_id(agent_id)
        _INBOX_CACHE.pop(norm, None)
        _OUTBOX_CACHE.pop(norm, None)
        for p in (get_inbox_path(norm), get_outbox_path(norm)):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass


def get_coach_inbox(agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns incoming A2A messages relevant to the strategic Coach (long-term consciousness)."""
    norm = normalize_agent_id(agent_id)
    all_msgs = get_inbox(norm, limit=max(limit * 2, 50))
    coach_msgs = []
    for m in all_msgs:
        s = str(m.get("sender_id", "")).lower()
        r = str(m.get("recipient_id", "")).lower()
        m_type = str(m.get("message_type", "")).upper()
        if "coach" in s or "coach" in r or r in ("*", "broadcast", "all") or m_type in ("PROPOSAL", "PACT", "COACH_MESSAGE"):
            coach_msgs.append(m)
    return coach_msgs[-limit:]


def build_default_agent_card(agent_id: str, port: int) -> AgentCard:
    """Constructs the authoritative Agent Card for a cat agent."""
    norm_id = normalize_agent_id(agent_id)
    base_url = f"http://127.0.0.1:{port}"
    card_url = f"{base_url}/.well-known/agent.json"

    display_name = f"Predator {norm_id.upper()}"
    description = (
        f"Autonomous tactical LLM predator agent '{norm_id}' running in the Cat & Mouse arena. "
        "Operates a dual-consciousness architecture: Short-Term Tactical Actor for agile movement & combat, "
        "and Long-Term Strategic Coach for macro evaluation, telemetry synthesis, and inter-coach A2A diplomacy."
    )

    return AgentCard(
        id=norm_id,
        name=display_name,
        description=description,
        version="1.0.0",
        protocol_version="a2a/v1",
        card_url=card_url,
        endpoints={
            "agent_card": card_url,
            "messages": f"{base_url}/a2a/messages",
            "inbox": f"{base_url}/a2a/inbox",
            "ws": f"ws://127.0.0.1:8000/ws/agents",
        },
        capabilities={
            "direct_messaging": True,
            "broadcast": True,
            "agent_discovery": True,
            "tools": True,
            "negotiation": True,
            "coach_to_coach_diplomacy": True,
        },
        skills=[
            AgentSkill(
                id="combat",
                name="Predator Combat",
                description="Lethal directional slash attack and lingering hazardous strike zone",
                tags=["combat", "lethal", "strike"],
            ),
            AgentSkill(
                id="stalking",
                name="Prey Stalking",
                description="Long-range pursuit, approach, and cornering mechanics",
                tags=["navigation", "stealth", "tracking"],
            ),
            AgentSkill(
                id="a2a_coordination",
                name="Agent-to-Agent Coordination",
                description="Peer-to-peer direct communication, alliance formation, and territorial bargaining",
                tags=["a2a", "communication", "negotiation"],
            ),
            AgentSkill(
                id="coach_diplomacy",
                name="Coach Strategic Diplomacy",
                description="Long-term consciousness inter-coach communication, macro territory bargaining, and peace pacts via A2A",
                tags=["a2a", "coach", "diplomacy", "macro_strategy"],
            ),
        ],
        metadata={
            "port": port,
            "arena": "Cat & Mouse",
            "model": "gemini-2.5-flash",
            "consciousness": {
                "short_term": {
                    "id": norm_id,
                    "role": "Tactical Actor (reflexive movement, chase, slash combat)",
                },
                "long_term": {
                    "id": f"coach({norm_id})",
                    "role": "Strategic Coach (long-horizon evaluation, telemetry synthesis, and inter-coach A2A diplomacy)",
                },
            },
        },
    )
