"""Entrypoint to run the Cat & Mouse MCP Server."""

import argparse
import logging
import os
import uvicorn

from app.mcp.server import create_mcp_app, DEFAULT_MCP_PORT, MCP_PORT_ENV

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cat_mcp")


def main():
    parser = argparse.ArgumentParser(description="Cat & Mouse MCP Server")
    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("CAT_MCP_HOST", "127.0.0.1"),
        help="Host to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get(MCP_PORT_ENV, DEFAULT_MCP_PORT)),
        help=f"Port to listen on (default: {DEFAULT_MCP_PORT})",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload",
    )
    args = parser.parse_args()

    print("=================================================================")
    print("🔌 Cat & Mouse MCP Server (Model Context Protocol)")
    print("=================================================================")
    print(f"   • Live Dashboard UI:  http://{args.host}:{args.port}/")
    print(f"   • SSE Endpoint:       http://{args.host}:{args.port}/sse")
    print(f"   • Messages Endpoint:  http://{args.host}:{args.port}/messages/")
    print("=================================================================")

    if args.reload:
        uvicorn.run(
            "app.mcp.server:create_mcp_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=True,
            log_level="info",
        )
    else:
        app = create_mcp_app()
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

