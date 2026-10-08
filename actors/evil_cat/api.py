"""Evil Cat Workbench API.

Reuses the production Cat Agent API application from actors.cat.api,
ensuring 100% feature parity, shared WebSocket management, and consistent
multi-agent runtime store resolution.
"""

from actors.cat.api import app, state, ws_client

ws_manager = ws_client

__all__ = ["app", "state", "ws_client", "ws_manager"]
