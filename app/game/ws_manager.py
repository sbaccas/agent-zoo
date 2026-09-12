from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, TYPE_CHECKING
from fastapi import WebSocket
from app.models.entity import EntityType

if TYPE_CHECKING:
    from app.game.simulation import Simulation

logger = logging.getLogger("game.ws_manager")


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

                # Clear action buffer
                self.action_buffers.pop(agent_id, None)

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
    ) -> bool:
        """Asynchronously dispatches an observation to the connected agent without blocking."""
        websocket = self.active_connections.get(agent_id)
        if not websocket:
            return False
        try:
            await websocket.send_json({
                "type": "observation",
                "tick": obs_payload.get("tick"),
                "round": obs_payload.get("round"),
                "payload": obs_payload,
            })
            return True
        except Exception as exc:
            logger.warning(f"⚠️ [WS AGENT {agent_id}] Error dispatching observation: {exc}")
            return False

    async def request_action_with_budget(
        self,
        agent_id: str,
        obs_payload: Dict[str, Any],
        budget_sec: float = 0.5,
    ) -> Optional[Dict[str, Any]]:
        """
        Decoupled tick-budget deliberation:
        1. Dispatches the fresh observation to the agent.
        2. If an action is already buffered (e.g. from previous deliberation or fast reflex), returns it.
        3. Otherwise waits up to budget_sec for an action to arrive.
        4. If the budget elapses without an action, returns None (allowing scheduler to default to 'stay').
        """
        websocket = self.active_connections.get(agent_id)
        if not websocket:
            return None

        # 1. Dispatch observation
        sent = await self.send_observation(agent_id, obs_payload)
        if not sent:
            return None

        # 2. Check if an action was already queued in buffer
        buffered = self.pop_buffered_action(agent_id)
        if buffered:
            return buffered

        # 3. Wait up to budget_sec
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending_actions[agent_id] = fut

        try:
            action_data = await asyncio.wait_for(fut, timeout=budget_sec)
            self.action_buffers.pop(agent_id, None)
            return action_data
        except asyncio.TimeoutError:
            return None
        except Exception as exc:
            logger.warning(f"⚠️ [WS AGENT {agent_id}] Error waiting for action: {exc}")
            return None
        finally:
            self._pending_actions.pop(agent_id, None)

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
