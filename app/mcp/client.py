"""MCP Client bridge for querying the Cat & Mouse MCP Server over SSE transport."""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger("cat_mcp_client")

DEFAULT_MCP_SERVER_URL = "http://127.0.0.1:9050"
MCP_SERVER_URL_ENV = "CAT_MCP_SERVER_URL"
MCP_CLIENT_TIMEOUT_SEC = 5.0


def get_mcp_server_url() -> str:
    return (
        os.environ.get(MCP_SERVER_URL_ENV)
        or os.environ.get("MCP_SERVER_URL")
        or DEFAULT_MCP_SERVER_URL
    ).rstrip("/")


class MCPClient:
    """Async client managing connection to the C+M MCP server."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = MCP_CLIENT_TIMEOUT_SEC):
        self.base_url = (base_url or get_mcp_server_url()).rstrip("/")
        self.timeout = timeout
        self.sse_url = f"{self.base_url}/sse"

    def _get_headers(self) -> Optional[Dict[str, str]]:
        """Fetch GCP IAM ID token header for service-to-service calls if running in Cloud Run."""
        if self.base_url.startswith("http://127.0.0.1") or self.base_url.startswith("http://localhost"):
            return None
        audience = os.environ.get("MCP_SERVER_AUDIENCE") or os.environ.get("CAT_MCP_SERVER_AUDIENCE")
        if not audience:
            if self.base_url.startswith("https://"):
                audience = self.base_url
            elif "catz.internal" in self.base_url:
                audience = "https://mcp-server-seh2tezjxa-nn.a.run.app"
        try:
            from actors.auth import get_gcp_id_token
            token = get_gcp_id_token(audience=audience)
            if token:
                return {"Authorization": f"Bearer {token}"}
        except Exception as exc:
            logger.debug("Could not fetch ID token for MCP server: %s", exc)
        return None

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List all tools registered on the MCP server."""
        try:
            async with asyncio.timeout(self.timeout):
                async with sse_client(self.sse_url, headers=self._get_headers(), timeout=self.timeout) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        return [
                            {
                                "name": t.name,
                                "description": t.description,
                                "input_schema": getattr(t, "input_schema", {}),
                            }
                            for t in result.tools
                        ]
        except Exception as exc:
            logger.warning("MCP client failed to list tools: %s", exc)
            return []

    async def call_tool(
        self, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Call an MCP tool and return its structured output."""
        arguments = arguments or {}
        try:
            async with asyncio.timeout(self.timeout):
                async with sse_client(self.sse_url, headers=self._get_headers(), timeout=self.timeout) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        call_res = await session.call_tool(name, arguments=arguments)
                        if getattr(call_res, "is_error", False):
                            return {
                                "available": False,
                                "reason": f"MCP tool '{name}' reported error",
                            }
                        if hasattr(call_res, "structured_content") and call_res.structured_content:
                            sc = call_res.structured_content
                            if isinstance(sc, dict) and "result" in sc:
                                return sc["result"]
                            return sc
                        if getattr(call_res, "content", None):
                            first = call_res.content[0]
                            text = getattr(first, "text", "")
                            try:
                                return json.loads(text)
                            except Exception:
                                return {"result": text}
                        return {"available": True}
        except Exception as exc:
            logger.warning("MCP client failed calling tool %s: %s: %s", name, type(exc).__name__, exc)
            return {"available": False, "reason": f"MCP connection error: {type(exc).__name__}: {exc}"}

    async def list_resources(self) -> List[Dict[str, Any]]:
        """List all resources registered on the MCP server."""
        try:
            async with asyncio.timeout(self.timeout):
                async with sse_client(self.sse_url, headers=self._get_headers(), timeout=self.timeout) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.list_resources()
                        return [
                            {
                                "uri": str(r.uri),
                                "name": r.name,
                                "description": r.description,
                                "mime_type": r.mime_type,
                            }
                            for r in result.resources
                        ]
        except Exception as exc:
            logger.warning("MCP client failed to list resources: %s", exc)
            return []

    async def read_resource(self, uri: str) -> Optional[Any]:
        """Read an MCP resource by URI."""
        try:
            async with asyncio.timeout(self.timeout):
                async with sse_client(self.sse_url, headers=self._get_headers(), timeout=self.timeout) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        res = await session.read_resource(uri)
                        contents = getattr(res, "contents", [])
                        if contents:
                            first = contents[0]
                            content = getattr(first, "text", None) or getattr(first, "content", None) or ""
                            try:
                                return json.loads(content)
                            except Exception:
                                return content
                        return None
        except Exception as exc:
            logger.warning("MCP client failed to read resource %s: %s", uri, exc)
            return None


# Global convenience client
_default_client: Optional[MCPClient] = None


def get_default_mcp_client() -> MCPClient:
    global _default_client
    if _default_client is None:
        _default_client = MCPClient()
    return _default_client
