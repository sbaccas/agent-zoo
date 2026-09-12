import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.api.admin import get_simulation_or_404
from app.game.ws_manager import ws_agent_manager

logger = logging.getLogger("api.ws")
router = APIRouter(tags=["WebSockets"])


@router.websocket("/ws/agents")
async def agents_websocket_endpoint(websocket: WebSocket, sim_id: str = "active"):
    """
    WebSocket endpoint for external agents (Cats, Mice) to connect to the Game Server.

    Protocol Flow:
      1. Client initiates connection and sends a 'register' handshake payload:
         { "type": "register", "role": "cat", "agent_name": "gemini-cat", "requested_id": "cat-1" }
      2. Server binds the socket to an available entity and responds:
         { "type": "registered", "status": "ok", "agent_id": "cat-1", "simulation_id": "...", ... }
      3. During each simulation tick, the server pushes:
         { "type": "observation", "tick": 42, "round": 1, "payload": { ... } }
      4. The agent responds with:
         { "type": "action", "tick": 42, "action": "lunge", "thought": "..." }
      5. Heartbeat: client may send { "type": "ping" } and receive { "type": "pong" }.
    """
    await websocket.accept()
    sim = get_simulation_or_404(sim_id)
    assigned_id = None

    try:
        # Step 1: Handshake
        init_data = await websocket.receive_json()
        msg_type = init_data.get("type")
        if msg_type != "register":
            await websocket.send_json({
                "type": "error",
                "message": f"Expected 'register' handshake message, got '{msg_type}'",
            })
            await websocket.close()
            return

        role = init_data.get("role", "cat")
        agent_name = init_data.get("agent_name", "agent")
        requested_id = init_data.get("requested_id")

        try:
            assigned_id = await ws_agent_manager.register(
                websocket=websocket,
                role=role,
                agent_name=agent_name,
                requested_id=requested_id,
                sim=sim,
            )
        except ValueError as err:
            await websocket.send_json({
                "type": "error",
                "message": str(err),
            })
            await websocket.close()
            return

        # Prepare initial observation so the agent immediately knows its position & surroundings
        entity = sim.entity_manager.entities.get(assigned_id)
        obs_payload = sim.scheduler._build_observation_payload(entity) if entity else {}

        # Send registration confirmation
        await websocket.send_json({
            "type": "registered",
            "status": "ok",
            "agent_id": assigned_id,
            "role": role,
            "simulation_id": sim.simulation_id,
            "world": {
                "width": sim.world.width,
                "height": sim.world.height,
            },
            "initial_observation": obs_payload,
        })

        # Step 2: Message listening loop
        while True:
            data = await websocket.receive_json()
            mtype = data.get("type")

            if mtype == "action":
                ws_agent_manager.receive_action(assigned_id, data)
            elif mtype == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": data.get("timestamp"),
                })
            else:
                logger.debug(f"Received unknown message type '{mtype}' from WS agent {assigned_id}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket agent disconnected for {assigned_id or 'unknown'}")
    except Exception as exc:
        logger.warning(f"WebSocket error for agent {assigned_id}: {exc}")
    finally:
        await ws_agent_manager.unregister(websocket, sim=sim)
