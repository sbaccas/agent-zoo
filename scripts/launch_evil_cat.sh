#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

export PORT="${PORT:-8005}"
export CAT_ID="${CAT_ID:-cat-3}"
export CAT_SKILLS_PATH="${CAT_SKILLS_PATH:-actors/evil_cat/skills.md}"
export EVAL_SKILLS_PATH="${EVAL_SKILLS_PATH:-actors/evil_cat/coach_skills.md}"
export CAT_EVAL_INTERVAL="${CAT_EVAL_INTERVAL:-5}"
export CAT_HOST="${HOST:-127.0.0.1}"

# Check if target port is already running and terminate existing listeners
PIDS_PORT=$(lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "${PIDS_PORT}" ]; then
  for pid in ${PIDS_PORT}; do
    kill -9 "${pid}" 2>/dev/null || true
  done
  sleep 1
fi

echo "================================================================="
echo "😈 Evil Cat (cat-3) Fast-Brain & Conductor Launcher"
echo "   • Agent Identity:             ${CAT_ID}"
echo "   • Workbench Dashboard:        http://${CAT_HOST}:${PORT}"
echo "   • Tactical Skills:            ${CAT_SKILLS_PATH}"
echo "   • Conductor Skills:           ${EVAL_SKILLS_PATH}"
echo "   • Evaluation Interval:        ${CAT_EVAL_INTERVAL} ticks (non-blocking)"
echo "================================================================="

exec python -m uvicorn actors.evil_cat.api:app --host "${CAT_HOST}" --port "${PORT}" --reload --no-access-log
