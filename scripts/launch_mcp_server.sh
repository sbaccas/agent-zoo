#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

echo "================================================================="
echo "🔌 Cat & Mouse MCP Server Launcher (Model Context Protocol)"
echo "================================================================="

# 1. Activate virtual environment
if [ -d ".venv" ]; then
  source .venv/bin/activate
  echo "✓ Activated virtual environment (.venv)"
else
  echo "❌ Error: Virtual environment .venv not found in ${ROOT_DIR}"
  exit 1
fi

# 2. Source .env if present
if [ -f ".env" ]; then
  set -a
  source .env
  set +a
  echo "✓ Loaded environment configuration (.env)"
fi

# 3. Determine host and port
MCP_PORT="${CAT_MCP_PORT:-${PORT:-9050}}"
MCP_HOST="${CAT_MCP_HOST:-${HOST:-127.0.0.1}}"

# Check if target port is already running and terminate existing listeners
PIDS_PORT=$(lsof -ti "tcp:${MCP_PORT}" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "${PIDS_PORT}" ]; then
  echo ""
  for pid in ${PIDS_PORT}; do
    echo "🔄 Stopping existing process on port ${MCP_PORT} (PID: ${pid})..."
    kill -9 "${pid}" 2>/dev/null || true
  done
  sleep 1
fi

echo ""
echo "🚀 Starting Cat & Mouse MCP Server on port ${MCP_PORT}..."
echo "   • Live Dashboard UI:  http://${MCP_HOST}:${MCP_PORT}/"
echo "   • SSE Endpoint:       http://${MCP_HOST}:${MCP_PORT}/sse"
echo "   • Messages Endpoint:  http://${MCP_HOST}:${MCP_PORT}/messages/"
echo "================================================================="
echo ""

exec python -m app.mcp.run --host "${MCP_HOST}" --port "${MCP_PORT}" --reload
