#!/usr/bin/env bash
# Convenience root wrapper for scripts/start_game.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/scripts/start_game.sh" "$@"
