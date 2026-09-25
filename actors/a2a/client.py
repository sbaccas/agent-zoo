"""Client transport for Agent-to-Agent (A2A) protocol communication."""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional
import httpx

from actors.a2a.models import A2AMessage, A2AResponse, AgentCard
from actors.a2a import store


# Known peer directory (can be overridden via environment or dynamic registration)
DEFAULT_PEER_REGISTRY: Dict[str, str] = {
    "cat-1": "http://127.0.0.1:8001",
    "cat-2": "http://127.0.0.1:8003",
}


def get_peer_registry() -> Dict[str, str]:
    """Returns the map of agent_id -> base_url."""
    env_peers = os.environ.get("CAT_PEER_REGISTRY")
    if env_peers:
        try:
            return json.loads(env_peers)
        except Exception:
            pass
    return dict(DEFAULT_PEER_REGISTRY)


def resolve_peer_base_url(agent_id: str) -> Optional[str]:
    """Resolves the base URL for an agent or coach ID."""
    reg = get_peer_registry()
    if agent_id in reg:
        return reg[agent_id]
    
    # Handle coach/actor prefix or formatting (e.g. 'coach(cat-1)', 'coach_cat-2', 'cat-1-coach')
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
    if clean in reg:
        return reg[clean]
    return None


async def fetch_agent_card(agent_id: str, timeout: float = 3.0) -> Optional[AgentCard]:
    """Queries an agent's authoritative Agent Card from /.well-known/agent.json."""
    base_url = resolve_peer_base_url(agent_id)
    if not base_url:
        return None

    card_url = f"{base_url.rstrip('/')}/.well-known/agent.json"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(card_url)
            if resp.status_code == 200:
                return AgentCard.model_validate(resp.json())
    except Exception as exc:
        print(f"[A2A Client] Failed to fetch agent card for {agent_id} from {card_url}: {exc}", flush=True)
    return None


async def send_a2a_message(
    sender_id: str,
    recipient_id: str,
    content: str,
    message_type: str = "DIRECT_MESSAGE",
    tick: Optional[int] = None,
    round_num: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    timeout: float = 4.0,
) -> A2AResponse:
    """Dispatches a structured A2A message to a recipient agent over HTTP POST."""
    sender_base = resolve_peer_base_url(sender_id) or "http://127.0.0.1:8001"
    sender_card_url = f"{sender_base.rstrip('/')}/.well-known/agent.json"

    msg = A2AMessage(
        sender_id=sender_id,
        sender_card_url=sender_card_url,
        recipient_id=recipient_id,
        message_type=message_type,
        content=content,
        tick=tick,
        round=round_num,
        metadata=metadata or {},
    )

    # 1. Store in sender's local outbox
    store.append_outbox(sender_id, msg)

    # 2. Telemetry broadcast to Game Server (for UI and MCP observer stream)
    game_server_url = os.environ.get("GAME_SERVER_URL", "http://127.0.0.1:8000")
    asyncio.create_task(_report_transmission_to_engine(game_server_url, msg))

    # 3. Direct transmission to recipient
    if recipient_id in ("*", "broadcast", "all"):
        # Broadcast to all known peers other than sender
        reg = get_peer_registry()
        peers = [pid for pid in reg if pid != sender_id]
        delivered_count = 0
        errors = []
        for pid in peers:
            p_base = reg[pid]
            p_url = f"{p_base.rstrip('/')}/a2a/messages"
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(p_url, json=msg.model_dump())
                    if resp.status_code == 200:
                        delivered_count += 1
                    else:
                        errors.append(f"{pid}: HTTP {resp.status_code}")
            except Exception as exc:
                errors.append(f"{pid}: {str(exc)}")

        status = "delivered" if delivered_count > 0 else "error"
        err_str = "; ".join(errors) if errors else None
        return A2AResponse(
            status=status,
            message_id=msg.message_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            error=err_str,
        )

    # Direct Message (DM) to single peer
    peer_base = resolve_peer_base_url(recipient_id)
    if not peer_base:
        err = f"Unknown peer agent: {recipient_id}"
        return A2AResponse(
            status="error",
            message_id=msg.message_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            error=err,
        )

    endpoint_url = f"{peer_base.rstrip('/')}/a2a/messages"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint_url, json=msg.model_dump())
            if resp.status_code == 200:
                data = resp.json()
                return A2AResponse.model_validate(data)
            else:
                err = f"Peer returned HTTP {resp.status_code}: {resp.text}"
                return A2AResponse(
                    status="error",
                    message_id=msg.message_id,
                    sender_id=sender_id,
                    recipient_id=recipient_id,
                    error=err,
                )
    except Exception as exc:
        err = f"Network failure communicating with {recipient_id} at {endpoint_url}: {exc}"
        return A2AResponse(
            status="error",
            message_id=msg.message_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            error=err,
        )


async def _report_transmission_to_engine(game_server_url: str, msg: A2AMessage) -> None:
    """Sends telemetry record to game engine comms log."""
    endpoint = f"{game_server_url.rstrip('/')}/api/simulations/active/comms"
    payload = {
        "sender_id": msg.sender_id,
        "recipient_id": msg.recipient_id,
        "message": msg.content,
        "tick": msg.tick,
        "round": msg.round,
        "type": msg.message_type,
        "protocol": msg.protocol,
        "message_id": msg.message_id,
    }
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(endpoint, json=payload)
    except Exception:
        pass
