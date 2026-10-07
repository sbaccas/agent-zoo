#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PORT="${PORT:-8005}"
export CAT_ID="${CAT_ID:-cat-3}"
export CAT_SKILLS_PATH="${CAT_SKILLS_PATH:-actors/cat/evil_skills.md}"
export EVAL_SKILLS_PATH="${EVAL_SKILLS_PATH:-actors/cat/subagents/evil_coach_skills.md}"
export CAT_EVAL_INTERVAL="${CAT_EVAL_INTERVAL:-5}"

exec "${SCRIPT_DIR}/launch_workbench.sh"
