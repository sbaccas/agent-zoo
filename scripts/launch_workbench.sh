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

# 4. Check if port 8001 is already running and terminate existing listeners
PIDS_8001=$(lsof -ti tcp:8001 -sTCP:LISTEN 2>/dev/null || true)
if [ -n "${PIDS_8001}" ]; then
  echo ""
  for pid in ${PIDS_8001}; do
    echo "🔄 Stopping existing process on port 8001 (PID: ${pid})..."
    kill -9 "${pid}" 2>/dev/null || true
  done
  sleep 1
fi

echo ""
echo "🚀 Starting Cat Agent & Workbench on port 8001..."
echo "   • Workbench Dashboard:        http://127.0.0.1:8001"
echo "   • Authoritative Game Server:  http://127.0.0.1:8000"
echo "================================================================="
echo ""

exec python -m uvicorn actors.cat.api:app --host 127.0.0.1 --port 8001 --reload --no-access-log

