"""ADK Function Tools for Agent-to-Agent (A2A) protocol interaction."""

import asyncio
from contextvars import ContextVar
import json
from typing import Any, Dict, List, Optional

from actors.a2a import client, store
from actors.a2a.models import A2AMessage

# Context tracking for the current tick deliberation
ctx_current_agent_id: ContextVar[str] = ContextVar("ctx_current_agent_id", default="cat-1")
ctx_current_tick: ContextVar[int] = ContextVar("ctx_current_tick", default=0)
ctx_current_round: ContextVar[int] = ContextVar("ctx_current_round", default=0)
ctx_tool_calls_trace: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar("ctx_tool_calls_trace", default=None)
ctx_current_eliminated_cats: ContextVar[List[str]] = ContextVar("ctx_current_eliminated_cats", default=[])


def set_deliberation_context(
    agent_id: str,
    tick: int,
    round_num: int = 0,
    eliminated_cats: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Initializes context variables for a deliberation run and returns a tool trace buffer."""
    trace: List[Dict[str, Any]] = []
    ctx_current_agent_id.set(agent_id)
    ctx_current_tick.set(tick)
    ctx_current_round.set(round_num)
    ctx_tool_calls_trace.set(trace)
    ctx_current_eliminated_cats.set(list(eliminated_cats or []))
    return trace


def _run_sync(coro, timeout: float = 4.0):
    """Safely executes a coroutine synchronously regardless of ambient loop state."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=timeout)
    else:
        return asyncio.run(coro)


def a2a_send_message(recipient_id: str, message: str, message_type: str = "DIRECT_MESSAGE") -> str:
    """Send an Agent-to-Agent (A2A) message directly to another agent on the board.

    Use this tool to communicate with a specific peer agent (e.g. 'cat-2' or 'cat-1').
    Direct messages are delivered privately to the recipient's A2A inbox.

    Args:
        recipient_id: Target agent identifier (e.g. 'cat-2').
        message: The textual message content to transmit.
        message_type: Message type ('DIRECT_MESSAGE', 'PROPOSAL', or 'ALERT'). Default is 'DIRECT_MESSAGE'.

    Returns:
        Delivery confirmation status string.
    """
    sender_id = ctx_current_agent_id.get()
    tick = ctx_current_tick.get()
    round_num = ctx_current_round.get()

    clean_recip = store.normalize_agent_id(recipient_id)

    # Pre-check: reject immediately if recipient is not a registered A2A agent (no Agent Card)
    if clean_recip not in ("*", "broadcast", "all") and not client.resolve_peer_base_url(clean_recip):
        err = f"Recipient {recipient_id} is not a registered A2A agent (no Agent Card found)."
        result_record = {
            "tool": "a2a_send_message",
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "message": message,
            "message_type": message_type,
            "tick": tick,
            "status": "undeliverable",
            "message_id": None,
            "error": err,
        }
        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append(result_record)
        return f"Delivery failed: recipient {recipient_id} is not a registered A2A agent (no Agent Card found)."

    # Pre-check: reject immediately if recipient is already dead/eliminated
    eliminated = ctx_current_eliminated_cats.get()
    if recipient_id in eliminated or clean_recip in eliminated:
        err = f"Recipient {recipient_id} has been eliminated from the arena and cannot receive messages."
        result_record = {
            "tool": "a2a_send_message",
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "message": message,
            "message_type": message_type,
            "tick": tick,
            "status": "undeliverable",
            "message_id": None,
            "error": err,
        }
        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append(result_record)
        return f"Delivery failed: recipient {recipient_id} is dead/eliminated from the arena."

    # OPSEC guard: block leaking internal commander orders or addressing command authority
    from actors.a2a.models import check_opsec_violation
    opsec_err = check_opsec_violation(message)
    if opsec_err:
        result_record = {
            "tool": "a2a_send_message",
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "message": message,
            "message_type": message_type,
            "tick": tick,
            "status": "rejected",
            "message_id": None,
            "error": opsec_err,
        }
        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append(result_record)
        return f"Delivery rejected: {opsec_err}"

    try:
        resp = _run_sync(
            client.send_a2a_message(
                sender_id=sender_id,
                recipient_id=recipient_id,
                content=message,
                message_type=message_type,
                tick=tick,
                round_num=round_num,
            ),
            timeout=4.0,
        )

        result_record = {
            "tool": "a2a_send_message",
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "message": message,
            "message_type": message_type,
            "tick": tick,
            "status": resp.status,
            "message_id": resp.message_id,
            "error": resp.error,
        }

        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append(result_record)

        if resp.status == "delivered":
            return f"A2A {message_type} delivered to {recipient_id} (Message ID: {resp.message_id})"
        elif resp.status == "undeliverable":
            return f"Delivery failed: recipient {recipient_id} is dead/eliminated from the arena. ({resp.error})"
        else:
            return f"A2A dispatch completed with status '{resp.status}': {resp.error or 'no additional error'}"

    except Exception as exc:
        err_msg = f"Failed to dispatch A2A message to {recipient_id}: {exc}"
        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append({
                "tool": "a2a_send_message",
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "message": message,
                "tick": tick,
                "status": "error",
                "error": str(exc),
            })
        return err_msg


def a2a_broadcast(message: str) -> str:
    """Broadcast an Agent-to-Agent (A2A) message to all agents across the arena.

    Publicly broadcasts a message across the arena. All peer agents will receive this in their A2A inbox.

    Args:
        message: The message content to broadcast.

    Returns:
        Broadcast confirmation status string.
    """
    sender_id = ctx_current_agent_id.get()
    tick = ctx_current_tick.get()
    round_num = ctx_current_round.get()
    # OPSEC guard: block leaking internal commander orders or addressing command authority
    from actors.a2a.models import check_opsec_violation
    opsec_err = check_opsec_violation(message)
    if opsec_err:
        result_record = {
            "tool": "a2a_broadcast",
            "sender_id": sender_id,
            "recipient_id": "*",
            "message": message,
            "message_type": "BROADCAST",
            "tick": tick,
            "status": "rejected",
            "message_id": None,
            "error": opsec_err,
        }
        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append(result_record)
        return f"Broadcast rejected: {opsec_err}"

    try:
        resp = _run_sync(
            client.send_a2a_message(
                sender_id=sender_id,
                recipient_id="*",
                content=message,
                message_type="BROADCAST",
                tick=tick,
                round_num=round_num,
            ),
            timeout=4.0,
        )

        result_record = {
            "tool": "a2a_broadcast",
            "sender_id": sender_id,
            "recipient_id": "*",
            "message": message,
            "tick": tick,
            "status": resp.status,
            "message_id": resp.message_id,
            "error": resp.error,
        }

        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append(result_record)

        return f"A2A arena broadcast dispatched (Message ID: {resp.message_id}, status: {resp.status})"

    except Exception as exc:
        err_msg = f"Failed to broadcast A2A message: {exc}"
        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append({
                "tool": "a2a_broadcast",
                "sender_id": sender_id,
                "recipient_id": "*",
                "message": message,
                "tick": tick,
                "status": "error",
                "error": str(exc),
            })
        return err_msg


def a2a_get_agent_card(agent_id: str) -> str:
    """Inspect a peer agent's official A2A Agent Card.

    Discovers the peer's declared identity, protocol version, endpoints, and capabilities.

    Args:
        agent_id: Agent identifier to query (e.g. 'cat-2' or 'cat-1').

    Returns:
        JSON representation of the Agent Card, or error message if unavailable.
    """
    try:
        card = _run_sync(client.fetch_agent_card(agent_id=agent_id), timeout=3.0)

        if not card:
            return f"Agent Card for '{agent_id}' could not be retrieved or peer is offline."

        trace = ctx_tool_calls_trace.get()
        if trace is not None:
            trace.append({
                "tool": "a2a_get_agent_card",
                "agent_id": agent_id,
                "card": card.model_dump(),
            })

        return json.dumps(card.model_dump(), indent=2)

    except Exception as exc:
        return f"Error retrieving agent card for '{agent_id}': {exc}"


# Export list of ADK function tools
A2A_TOOLS = [a2a_send_message, a2a_broadcast, a2a_get_agent_card]
