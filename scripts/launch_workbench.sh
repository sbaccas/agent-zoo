#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

echo "================================================================="
echo "🐱 Cat Agent & Interactive Workbench Launcher (Vertex AI Option B)"
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
  echo "✓ Loaded environment configuration (.env):"
  echo "    • GOOGLE_CLOUD_PROJECT = ${GOOGLE_CLOUD_PROJECT:-not set}"
  echo "    • GOOGLE_CLOUD_LOCATION = ${GOOGLE_CLOUD_LOCATION:-not set}"
  echo "    • GOOGLE_GENAI_USE_VERTEXAI = ${GOOGLE_GENAI_USE_VERTEXAI:-not set}"
fi

# 3. Check Google Cloud Application Default Credentials (ADC)
echo ""
echo "🔍 Checking Google Cloud authentication..."
if ! command -v gcloud &> /dev/null; then
  echo "⚠️ 'gcloud' CLI was not found in PATH."
  echo "   Make sure Google Cloud SDK is installed if using Vertex AI."
else
  # Test if ADC token is currently valid
  if gcloud auth application-default print-access-token >/dev/null 2>&1; then
    echo "✓ Google Cloud Application Default Credentials are valid and ready!"
  else
    echo "⚠️ Application Default Credentials require authentication."
    echo "   Starting interactive login..."
    echo ""
    gcloud auth application-default login
    echo "✓ Credentials updated successfully!"
  fi
fi

# 4. Determine host, port, and game server URL
CAT_PORT="${PORT:-${CAT_AGENT_PORT:-8001}}"
CAT_HOST="${HOST:-127.0.0.1}"
GAME_URL="${GAME_SERVER_URL:-http://127.0.0.1:8000}"

# Check if target port is already running and terminate existing listeners
PIDS_PORT=$(lsof -ti "tcp:${CAT_PORT}" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "${PIDS_PORT}" ]; then
  echo ""
  for pid in ${PIDS_PORT}; do
    echo "🔄 Stopping existing process on port ${CAT_PORT} (PID: ${pid})..."
    kill -9 "${pid}" 2>/dev/null || true
  done
  sleep 1
fi

echo ""
echo "🚀 Starting Cat Agent & Workbench on port ${CAT_PORT}..."
echo "   • Workbench Dashboard:        http://${CAT_HOST}:${CAT_PORT}"
echo "   • Authoritative Game Server:  ${GAME_URL}"
echo "================================================================="
echo ""

exec python -m uvicorn actors.cat.api:app --host "${CAT_HOST}" --port "${CAT_PORT}" --reload --no-access-log

