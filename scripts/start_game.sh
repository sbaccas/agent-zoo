#!/usr/bin/env bash
# ==============================================================================
# 🐱🐭 Cat & Mouse — Full-Stack Arena & Multi-Agent Launcher
# ==============================================================================
# Starts the complete Cat & Mouse stack on localhost (127.0.0.1):
#   1. Authoritative Game Server          -> http://127.0.0.1:8000
#   2. MCP Telemetry Server               -> http://127.0.0.1:9050
#   3. Cat-1 Agent & Workbench            -> http://127.0.0.1:8001
#   4. Cat-2 Agent & Workbench            -> http://127.0.0.1:8003
#   5. Cat-3 (Evil Cat) Agent & Workbench -> http://127.0.0.1:8005
#   6. Optional Extra Cat Agents (cat-4+) -> http://127.0.0.1:8006, 8007, ...
#
# Usage:
#   ./scripts/start_game.sh [OPTIONS]
#
# Options:
#   --extra-cats N       Launch N additional LLM Cat Agent servers (cat-4, cat-5, ...)
#                        on incrementing ports starting after 8005 (8006, 8007, ...)
#   --cats N             Total number of LLM Cat Agent servers to run (minimum 3;
#                        e.g. --cats 5 launches cat-1..cat-3 plus cat-4 and cat-5)
#   --board-cats N       Total cats on the board (agents + heuristic NPCs; default: max(10, agent_count))
#   --mice N             Initial mouse population on the board (default: 250)
#   --eval-interval N    Coach evaluation interval in ticks (default: 5)
#   --auto-start         Automatically start the simulation clock once all agents connect
#   --no-mcp             Do not start the MCP server on port 9050
#   --no-reload          Disable uvicorn --reload (recommended when running many agents)
#   --stop               Stop all running Cat & Mouse services and exit
#   --status             Show status of all Cat & Mouse ports and exit
#   -h, --help           Show this help message and exit
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

HOST="${HOST:-127.0.0.1}"
GAME_PORT="${GAME_PORT:-8000}"
MCP_PORT="${CAT_MCP_PORT:-9050}"
CAT1_PORT="${CAT1_PORT:-8001}"
CAT2_PORT="${CAT2_PORT:-8003}"
CAT3_PORT="${CAT3_PORT:-8005}"
EXTRA_PORT_START="${EXTRA_PORT_START:-8006}"

EXTRA_CATS=0
BOARD_CATS=""
MICE_COUNT=""
EVAL_INTERVAL="${CAT_EVAL_INTERVAL:-5}"
AUTO_START=0
START_MCP=1
USE_RELOAD=1
ACTION="start"

LOG_DIR="${ROOT_DIR}/logs"
PID_FILE="${LOG_DIR}/arena_stack.pids"

# ANSI colors
BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
MAGENTA="\033[0;35m"
RESET="\033[0m"

print_help() {
  sed -n '2,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --extra-cats)
      EXTRA_CATS="${2:?--extra-cats requires a non-negative integer}"
      shift 2
      ;;
    --extra-cats=*)
      EXTRA_CATS="${1#*=}"
      shift
      ;;
    --cats)
      TOTAL_REQ="${2:?--cats requires an integer >= 3}"
      if [[ "${TOTAL_REQ}" -lt 3 ]]; then
        echo -e "${RED}❌ --cats must be at least 3 (for cat-1, cat-2, cat-3).${RESET}"
        exit 1
      fi
      EXTRA_CATS=$((TOTAL_REQ - 3))
      shift 2
      ;;
    --cats=*)
      TOTAL_REQ="${1#*=}"
      if [[ "${TOTAL_REQ}" -lt 3 ]]; then
        echo -e "${RED}❌ --cats must be at least 3 (for cat-1, cat-2, cat-3).${RESET}"
        exit 1
      fi
      EXTRA_CATS=$((TOTAL_REQ - 3))
      shift
      ;;
    --board-cats)
      BOARD_CATS="${2:?--board-cats requires an integer}"
      shift 2
      ;;
    --mice)
      MICE_COUNT="${2:?--mice requires an integer}"
      shift 2
      ;;
    --eval-interval)
      EVAL_INTERVAL="${2:?--eval-interval requires an integer}"
      shift 2
      ;;
    --auto-start)
      AUTO_START=1
      shift
      ;;
    --no-mcp)
      START_MCP=0
      shift
      ;;
    --no-reload)
      USE_RELOAD=0
      shift
      ;;
    --stop)
      ACTION="stop"
      shift
      ;;
    --status)
      ACTION="status"
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo -e "${RED}❌ Unknown option: $1${RESET}"
      print_help
      exit 1
      ;;
  esac
done

kill_port() {
  local port="$1"
  local label="${2:-port ${port}}"
  local pids
  pids=$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "${pids}" ]]; then
    for pid in ${pids}; do
      echo -e "  ${YELLOW}🔄 Stopping ${label} on :${port} (PID ${pid})...${RESET}"
      kill -TERM "${pid}" 2>/dev/null || true
    done
    sleep 0.4
    pids=$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "${pids}" ]]; then
      for pid in ${pids}; do
        kill -9 "${pid}" 2>/dev/null || true
      done
    fi
  fi
}

stop_all_services() {
  echo -e "${BOLD}${CYAN}🛑 Stopping Cat & Mouse Arena Stack...${RESET}"
  if [[ -f "${PID_FILE}" ]]; then
    while IFS=: read -r name pid port; do
      if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
        echo -e "  ${YELLOW}• Terminating ${name} (PID ${pid}, port :${port})${RESET}"
        kill -TERM "${pid}" 2>/dev/null || true
      fi
      if [[ -n "${port}" ]]; then
        kill_port "${port}" "${name}"
      fi
    done < "${PID_FILE}"
    rm -f "${PID_FILE}"
  fi
  kill_port "${GAME_PORT}" "Game Server"
  kill_port "${MCP_PORT}" "MCP Server"
  kill_port "${CAT1_PORT}" "cat-1"
  kill_port "${CAT2_PORT}" "cat-2"
  kill_port "${CAT3_PORT}" "cat-3 (Evil Cat)"
  echo -e "${GREEN}✓ All Cat & Mouse services stopped.${RESET}"
}

show_status() {
  echo -e "${BOLD}${CYAN}📊 Cat & Mouse Stack Status (${HOST})${RESET}"
  echo "-----------------------------------------------------------------"
  local ports=("${GAME_PORT}:Game Server" "${MCP_PORT}:MCP Server" "${CAT1_PORT}:cat-1" "${CAT2_PORT}:cat-2" "${CAT3_PORT}:cat-3 (Evil Cat)")
  if [[ -f "${PID_FILE}" ]]; then
    while IFS=: read -r name pid port; do
      if [[ "${port}" != "${GAME_PORT}" && "${port}" != "${MCP_PORT}" && "${port}" != "${CAT1_PORT}" && "${port}" != "${CAT2_PORT}" && "${port}" != "${CAT3_PORT}" ]]; then
        ports+=("${port}:${name}")
      fi
    done < "${PID_FILE}"
  fi
  for entry in "${ports[@]}"; do
    local p="${entry%%:*}"
    local lbl="${entry#*:}"
    local pid
    pid=$(lsof -ti "tcp:${p}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)
    if [[ -n "${pid}" ]]; then
      echo -e "  ${GREEN}● RUNNING${RESET}  :${p}  ${BOLD}${lbl}${RESET} (PID ${pid}) -> http://${HOST}:${p}"
    else
      echo -e "  ${DIM}○ STOPPED  :${p}  ${lbl}${RESET}"
    fi
  done
  echo "-----------------------------------------------------------------"
}

if [[ "${ACTION}" == "stop" ]]; then
  stop_all_services
  exit 0
elif [[ "${ACTION}" == "status" ]]; then
  show_status
  exit 0
fi

# 1. Activate virtualenv & load .env
if [[ -d ".venv" ]]; then
  source .venv/bin/activate
else
  echo -e "${RED}❌ Virtual environment (.venv) not found in ${ROOT_DIR}${RESET}"
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

mkdir -p "${LOG_DIR}"

# 2. Compute agent port assignments (cat-1=:8001, cat-2=:8003, cat-3=:8005, cat-4+=:8006, :8007, ...)
declare -a AGENT_IDS=("cat-1" "cat-2" "cat-3")
declare -a AGENT_PORTS=("${CAT1_PORT}" "${CAT2_PORT}" "${CAT3_PORT}")
declare -a AGENT_ROLES=("Standard Cat" "Standard Cat" "Evil Cat (150 HP, 2x Bounty)")

NEXT_PORT="${EXTRA_PORT_START}"
for ((i = 1; i <= EXTRA_CATS; i++)); do
  cid="cat-$((3 + i))"
  # Ensure port doesn't collide with MCP_PORT or reserved ports
  while [[ "${NEXT_PORT}" -eq "${GAME_PORT}" || "${NEXT_PORT}" -eq "${MCP_PORT}" || "${NEXT_PORT}" -eq "${CAT1_PORT}" || "${NEXT_PORT}" -eq "${CAT2_PORT}" || "${NEXT_PORT}" -eq "${CAT3_PORT}" ]]; do
    NEXT_PORT=$((NEXT_PORT + 1))
  done
  AGENT_IDS+=("${cid}")
  AGENT_PORTS+=("${NEXT_PORT}")
  AGENT_ROLES+=("Standard Cat (Extra #${i})")
  NEXT_PORT=$((NEXT_PORT + 1))
done

TOTAL_AGENTS="${#AGENT_IDS[@]}"

# Build CAT_PEER_REGISTRY JSON dynamically so every agent & coach knows every peer's URL
PEER_JSON="{"
for ((idx = 0; idx < TOTAL_AGENTS; idx++)); do
  cid="${AGENT_IDS[$idx]}"
  cport="${AGENT_PORTS[$idx]}"
  if [[ $idx -gt 0 ]]; then
    PEER_JSON+=","
  fi
  PEER_JSON+="\"${cid}\":\"http://${HOST}:${cport}\""
  if [[ "${cid}" == "cat-3" ]]; then
    PEER_JSON+=",\"evil-cat\":\"http://${HOST}:${cport}\""
  fi
done
PEER_JSON+="}"

export CAT_PEER_REGISTRY="${PEER_JSON}"
export GAME_SERVER_URL="http://${HOST}:${GAME_PORT}"
export CAT_MCP_SERVER_URL="http://${HOST}:${MCP_PORT}"
export CAT_EVAL_INTERVAL="${EVAL_INTERVAL}"

echo -e "${BOLD}${CYAN}=========================================================================${RESET}"
echo -e "${BOLD}🐱🐭 Cat & Mouse Arena — Full-Stack Launcher (${TOTAL_AGENTS} Autonomous Cat Agents)${RESET}"
echo -e "${BOLD}${CYAN}=========================================================================${RESET}"
echo -e "  • ${BOLD}Google Cloud Project:${RESET} ${GOOGLE_CLOUD_PROJECT:-not set (heuristic fallback if no API key)}"
echo -e "  • ${BOLD}Coach Eval Interval:${RESET}  Every ${CAT_EVAL_INTERVAL} ticks"
echo -e "  • ${BOLD}Peer Registry:${RESET}        ${DIM}${CAT_PEER_REGISTRY}${RESET}"
echo -e "  • ${BOLD}Logs Directory:${RESET}       ${LOG_DIR}/"
echo ""

# Clean up any previously running listeners on our target ports
kill_port "${GAME_PORT}" "Game Server"
if [[ "${START_MCP}" -eq 1 ]]; then
  kill_port "${MCP_PORT}" "MCP Server"
fi
for ((idx = 0; idx < TOTAL_AGENTS; idx++)); do
  kill_port "${AGENT_PORTS[$idx]}" "${AGENT_IDS[$idx]}"
done
rm -f "${PID_FILE}"

GAME_RELOAD_FLAGS=""
CAT_RELOAD_FLAGS=""
EVIL_CAT_RELOAD_FLAGS=""
MCP_RELOAD_FLAGS=""
if [[ "${USE_RELOAD}" -eq 1 ]]; then
  GAME_RELOAD_FLAGS="--reload --reload-dir app"
  CAT_RELOAD_FLAGS="--reload --reload-dir actors/cat --reload-dir actors/a2a"
  EVIL_CAT_RELOAD_FLAGS="--reload --reload-dir actors/evil_cat --reload-dir actors/a2a"
  MCP_RELOAD_FLAGS="--reload"
fi

wait_for_http() {
  local url="$1"
  local name="$2"
  local max_wait="${3:-20}"
  local elapsed=0
  while ! curl -fsS "${url}" >/dev/null 2>&1; do
    sleep 0.5
    elapsed=$((elapsed + 1))
    if [[ "${elapsed}" -ge $((max_wait * 2)) ]]; then
      echo -e "  ${RED}⚠️ Timed out waiting for ${name} at ${url}${RESET}"
      return 1
    fi
  done
  return 0
}

# 3. Start Authoritative Game Server (:8000)
echo -e "${BOLD}1️⃣  Starting Authoritative Game Server on http://${HOST}:${GAME_PORT} ...${RESET}"
python -m uvicorn app.main:app --host "${HOST}" --port "${GAME_PORT}" ${GAME_RELOAD_FLAGS} --no-access-log \
  > "${LOG_DIR}/game_server.log" 2>&1 &
GAME_PID=$!
echo "game-server:${GAME_PID}:${GAME_PORT}" >> "${PID_FILE}"
wait_for_http "http://${HOST}:${GAME_PORT}/api/simulations/active" "Game Server" 20
echo -e "   ${GREEN}✓ Game Server ready (PID ${GAME_PID})${RESET}"

# Configure board cat/mouse count if extra cats or explicit flags were provided
if [[ -n "${BOARD_CATS}" || -n "${MICE_COUNT}" || "${TOTAL_AGENTS}" -gt 10 ]]; then
  TARGET_CATS="${BOARD_CATS:-$(( TOTAL_AGENTS > 10 ? TOTAL_AGENTS : 10 ))}"
  TARGET_MICE="${MICE_COUNT:-250}"
  echo -e "   ${CYAN}⚙️  Configuring arena population: ${TARGET_CATS} cats, ${TARGET_MICE} mice...${RESET}"
  curl -fsS -X POST "http://${HOST}:${GAME_PORT}/api/simulations/active/population" \
    -H "Content-Type: application/json" \
    -d "{\"cats\": ${TARGET_CATS}, \"mice\": ${TARGET_MICE}}" >/dev/null 2>&1 || true
fi

# 4. Start MCP Server (:9050)
if [[ "${START_MCP}" -eq 1 ]]; then
  echo -e "${BOLD}2️⃣  Starting MCP Telemetry Server on http://${HOST}:${MCP_PORT} ...${RESET}"
  python -m app.mcp.run --host "${HOST}" --port "${MCP_PORT}" ${MCP_RELOAD_FLAGS} \
    > "${LOG_DIR}/mcp_server.log" 2>&1 &
  MCP_PID=$!
  echo "mcp-server:${MCP_PID}:${MCP_PORT}" >> "${PID_FILE}"
  wait_for_http "http://${HOST}:${MCP_PORT}/" "MCP Server" 20
  echo -e "   ${GREEN}✓ MCP Server ready (PID ${MCP_PID})${RESET}"
fi

# 5. Start Cat Agents (cat-1, cat-2, cat-3, plus any extra cats)
echo -e "${BOLD}3️⃣  Starting ${TOTAL_AGENTS} Autonomous Cat Agent Workbenches...${RESET}"
TMP_BASE="${TMPDIR:-/tmp}"

for ((idx = 0; idx < TOTAL_AGENTS; idx++)); do
  cid="${AGENT_IDS[$idx]}"
  cport="${AGENT_PORTS[$idx]}"
  crole="${AGENT_ROLES[$idx]}"
  runtime_dir="${TMP_BASE%/}/cm_runtime_${cid}"
  mkdir -p "${runtime_dir}"

  if [[ "${cid}" == "cat-3" ]]; then
    PORT="${cport}" \
    CAT_ID="${cid}" \
    CAT_AGENT_RUNTIME_DIR="${runtime_dir}" \
    CAT_SKILLS_PATH="actors/evil_cat/skills.md" \
    EVAL_SKILLS_PATH="actors/evil_cat/coach_skills.md" \
    CAT_EVAL_TIMEOUT_SEC="none" \
    python -m uvicorn actors.evil_cat.api:app --host "${HOST}" --port "${cport}" ${EVIL_CAT_RELOAD_FLAGS} --no-access-log \
      > "${LOG_DIR}/${cid}.log" 2>&1 &
  else
    PORT="${cport}" \
    CAT_ID="${cid}" \
    CAT_AGENT_RUNTIME_DIR="${runtime_dir}" \
    CAT_SKILLS_PATH="actors/cat/actor_skills.md" \
    EVAL_SKILLS_PATH="actors/cat/subagents/eval_skills.md" \
    CAT_EVAL_TIMEOUT_SEC="${CAT_EVAL_TIMEOUT_SEC:-900}" \
    python -m uvicorn actors.cat.api:app --host "${HOST}" --port "${cport}" ${CAT_RELOAD_FLAGS} --no-access-log \
      > "${LOG_DIR}/${cid}.log" 2>&1 &
  fi
  apid=$!
  echo "${cid}:${apid}:${cport}" >> "${PID_FILE}"
done

# Wait for each agent workbench to come online
for ((idx = 0; idx < TOTAL_AGENTS; idx++)); do
  cid="${AGENT_IDS[$idx]}"
  cport="${AGENT_PORTS[$idx]}"
  crole="${AGENT_ROLES[$idx]}"
  wait_for_http "http://${HOST}:${cport}/.well-known/agent.json" "${cid}" 25
  echo -e "   ${GREEN}✓ ${BOLD}${cid}${RESET}${GREEN} (${crole}) online at http://${HOST}:${cport}${RESET}"
done

if [[ "${AUTO_START}" -eq 1 ]]; then
  echo ""
  echo -e "${CYAN}▶️  Auto-starting simulation clock...${RESET}"
  curl -fsS -X POST "http://${HOST}:${GAME_PORT}/api/simulations/active/start" >/dev/null 2>&1 || true
fi

echo ""
echo -e "${BOLD}${GREEN}=========================================================================${RESET}"
echo -e "${BOLD}🎉 ALL SERVICES ONLINE & CONNECTED!${RESET}"
echo -e "${BOLD}${GREEN}=========================================================================${RESET}"
echo -e "  🏟️  ${BOLD}Arena Board UI:${RESET}        http://${HOST}:${GAME_PORT}"
echo -e "  📊  ${BOLD}Live Box Score:${RESET}        http://${HOST}:${GAME_PORT}/boxscore"
if [[ "${START_MCP}" -eq 1 ]]; then
  echo -e "  🔌  ${BOLD}MCP Server Dashboard:${RESET}  http://${HOST}:${MCP_PORT}"
fi
for ((idx = 0; idx < TOTAL_AGENTS; idx++)); do
  cid="${AGENT_IDS[$idx]}"
  cport="${AGENT_PORTS[$idx]}"
  crole="${AGENT_ROLES[$idx]}"
  echo -e "  🐱  ${BOLD}${cid} Workbench:${RESET}       http://${HOST}:${cport}   ${DIM}(${crole})${RESET}"
done
echo -e "${BOLD}${GREEN}=========================================================================${RESET}"
echo -e "${DIM}Press Ctrl+C to gracefully stop all services, or run './start_game.sh --stop' from another terminal.${RESET}"
echo ""

cleanup_on_exit() {
  echo ""
  stop_all_services
  exit 0
}
trap cleanup_on_exit INT TERM

# Keep script alive while child processes run
wait
