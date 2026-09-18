from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, TYPE_CHECKING
from fastapi import WebSocket
from app.models.entity import EntityType

if TYPE_CHECKING:
    from app.game.simulation import Simulation

logger = logging.getLogger("game.ws_manager")

# Collection outcomes. These are reported, not judged: the scheduler decides what
# to do about a miss, and the box score counts them.
COLLECT_FRESH = "fresh"
COLLECT_STALE = "stale"
COLLECT_MISSING = "missing"
COLLECT_NO_AGENT = "no_agent"


@dataclass(frozen=True)
class CollectedAction:
    """The result of asking for an agent's answer to the last observation sent.

    `status` distinguishes the three ways an agent can fail to be heard, which
    look identical to the caller otherwise but mean very different things:
    MISSING is a slow agent, STALE is an agent answering a question the board has
    already moved past, and NO_AGENT is nobody connected at all. Collapsing them
    into a bare `None` is what previously made a silent agent indistinguishable
    from a late one.
    """

    action: Optional[Dict[str, Any]]
    status: str
    answered_tick: Optional[int] = None
    expected_tick: Optional[int] = None

    @property
    def is_fresh(self) -> bool:
        return self.status == COLLECT_FRESH


class WsAgentManager:
    """Manages active WebSocket connections from external autonomous agents."""

    def __init__(self):
        # agent_id -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}
        # WebSocket -> agent_id
        self.connection_agents: Dict[WebSocket, str] = {}
        # agent_id -> metadata
        self.agent_metadata: Dict[str, Dict[str, Any]] = {}
        # agent_id -> asyncio.Future for pending actions
        self._pending_actions: Dict[str, asyncio.Future] = {}
        # agent_id -> latest buffered action from decoupled agents
        self.action_buffers: Dict[str, Dict[str, Any]] = {}
        # agent_id -> tick of the most recent observation dispatched to it.
        # This is the question currently on the table; an answer carrying any
        # other tick is answering a board state that no longer exists.
        self.last_dispatched_tick: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        websocket: WebSocket,
        role: str,
        agent_name: str,
        requested_id: Optional[str],
        sim: Simulation,
    ) -> str:
        """
        Binds a WebSocket connection to an available entity in the simulation.
        Sets the entity's mode to 'ws'.
        """
        async with self._lock:
            role_type = EntityType.CAT if role.lower() == "cat" else EntityType.MOUSE
            alive_entities = [
                e for e in sim.entity_manager.get_alive_entities()
                if e.type == role_type
            ]

            assigned_id: Optional[str] = None

            # 1. Try requested ID if specified and available
            if requested_id:
                matching = [
                    e for e in alive_entities
                    if e.id == requested_id and e.id not in self.active_connections
                ]
                if matching:
                    assigned_id = matching[0].id

            # 2. Otherwise, take the first available unassigned entity of this role
            if not assigned_id:
                for e in alive_entities:
                    if e.id not in self.active_connections:
                        assigned_id = e.id
                        break

            if not assigned_id:
                raise ValueError(
                    f"No available {role} entities to bind to. "
                    f"(Alive {role} count: {len(alive_entities)}, Connected: {len(self.active_connections)})"
                )

            self.active_connections[assigned_id] = websocket
            self.connection_agents[websocket] = assigned_id
            self.agent_metadata[assigned_id] = {
                "role": role,
                "agent_name": agent_name,
                "connected_at": time.time(),
            }
            sim.entity_manager.set_agent_mode(assigned_id, "ws")
            logger.info(
                f"🐾 [WS] Registered {role} agent '{agent_name}' to entity '{assigned_id}'"
            )
            return assigned_id

    async def unregister(self, websocket: WebSocket, sim: Optional[Simulation] = None) -> Optional[str]:
        """Cleans up a disconnected WebSocket and restores entity to auto mode."""
        async with self._lock:
            agent_id = self.connection_agents.pop(websocket, None)
            if agent_id:
                self.active_connections.pop(agent_id, None)
                self.agent_metadata.pop(agent_id, None)

                # Cancel any pending action requests
                fut = self._pending_actions.pop(agent_id, None)
                if fut and not fut.done():
                    fut.cancel()

                # Clear action buffer and the outstanding question. A reconnecting
                # agent must not be held to an observation sent to its predecessor.
                self.action_buffers.pop(agent_id, None)
                self.last_dispatched_tick.pop(agent_id, None)

                # Restore simulation entity to auto mode if still in ws mode
                if sim and sim.entity_manager.get_agent_mode(agent_id) == "ws":
                    sim.entity_manager.set_agent_mode(agent_id, "auto")

                logger.info(f"🐾 [WS] Unregistered agent '{agent_id}'")
            return agent_id

    def has_agent(self, agent_id: str) -> bool:
        """Returns True if an active WebSocket connection is registered for agent_id."""
        return agent_id in self.active_connections

    def get_agent_id(self, websocket: WebSocket) -> Optional[str]:
        return self.connection_agents.get(websocket)

    def receive_action(self, agent_id: str, action_data: Dict[str, Any]):
        """Buffers incoming action and delivers to any waiting scheduler future."""
        self.action_buffers[agent_id] = action_data
        fut = self._pending_actions.get(agent_id)
        if fut and not fut.done():
            fut.set_result(action_data)

    def pop_buffered_action(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Pops and returns the latest unconsumed action from the agent's buffer."""
        return self.action_buffers.pop(agent_id, None)

    def peek_buffered_action(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Inspects the latest unconsumed action without removing it."""
        return self.action_buffers.get(agent_id)

    async def send_observation(
        self,
        agent_id: str,
        obs_payload: Dict[str, Any],
        budget_sec: Optional[float] = None,
    ) -> bool:
        """Dispatches an observation and returns immediately. Never waits for a reply.

        `budget_sec` is sent as a *duration*, not an absolute deadline: the agent
        runs in a separate process, so a timestamp from this clock would mean
        nothing on the other end. The agent starts its own clock on receipt.

        Recording `last_dispatched_tick` here is what makes staleness detectable.
        The agent echoes the tick it is answering, so any reply that does not
        match the tick recorded here is answering a superseded board.
        """
        websocket = self.active_connections.get(agent_id)
        if not websocket:
            return False
        tick = obs_payload.get("tick")
        envelope: Dict[str, Any] = {
            "type": "observation",
            "tick": tick,
            "round": obs_payload.get("round"),
            "payload": obs_payload,
        }
        if budget_sec is not None:
            envelope["budget_sec"] = budget_sec
        try:
            await websocket.send_json(envelope)
            if tick is not None:
                self.last_dispatched_tick[agent_id] = tick
            return True
        except Exception as exc:
            logger.warning(f"⚠️ [WS AGENT {agent_id}] Error dispatching observation: {exc}")
            return False

    def collect_action(self, agent_id: str) -> CollectedAction:
        """Takes the agent's answer to the last observation sent, if it arrived in time.

        Non-blocking by construction. The deadline is not a timer here; it is this
        moment. Whatever is in the buffer when the scheduler asks is what the agent
        managed to deliver, and anything else is a miss.

        An answer carrying the wrong tick is discarded rather than used. Before
        this check the buffer was unbounded in age: a reply from several ticks ago
        executed just as readily as a fresh one, so the cat could act on a board
        that had already moved.
        """
        if agent_id not in self.active_connections:
            return CollectedAction(action=None, status=COLLECT_NO_AGENT)

        expected = self.last_dispatched_tick.get(agent_id)
        buffered = self.action_buffers.pop(agent_id, None)

        if buffered is None:
            return CollectedAction(
                action=None, status=COLLECT_MISSING, expected_tick=expected
            )

        answered = buffered.get("tick")
        # An agent that never learned to echo the tick cannot be verified. Treat
        # its answer as fresh rather than silently muting it: the alternative is a
        # third-party agent that connects successfully and is never once heard.
        if answered is None or expected is None or answered == expected:
            return CollectedAction(
                action=buffered,
                status=COLLECT_FRESH,
                answered_tick=answered,
                expected_tick=expected,
            )

        return CollectedAction(
            action=None,
            status=COLLECT_STALE,
            answered_tick=answered,
            expected_tick=expected,
        )

    async def request_action(
        self,
        agent_id: str,
        obs_payload: Dict[str, Any],
        timeout: float = 3.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Sends an observation to the connected agent and awaits an action response.
        Returns the action response dict, or None if timed out or failed.
        """
        websocket = self.active_connections.get(agent_id)
        if not websocket:
            return None

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending_actions[agent_id] = fut

        try:
            await websocket.send_json({
                "type": "observation",
                "tick": obs_payload.get("tick"),
                "round": obs_payload.get("round"),
                "payload": obs_payload,
            })
            action_data = await asyncio.wait_for(fut, timeout=timeout)
            return action_data
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ [WS AGENT {agent_id}] Timeout ({timeout}s) waiting for action")
            return None
        except Exception as exc:
            logger.warning(f"⚠️ [WS AGENT {agent_id}] Error requesting action: {exc}")
            return None
        finally:
            self._pending_actions.pop(agent_id, None)


# Global singleton instance
ws_agent_manager = WsAgentManager()
