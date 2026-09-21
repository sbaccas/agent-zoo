#!/usr/bin/env bash
set -e

CONFIG_FILE="$HOME/.ssh/config"
SOCKET_DIR="$HOME/.ssh"

mkdir -p "${SOCKET_DIR}"
chmod 700 "${SOCKET_DIR}"

if grep -q "scb.c.googlers.com" "${CONFIG_FILE}" 2>/dev/null; then
  echo "✓ Host scb.c.googlers.com is already configured in ${CONFIG_FILE}"
else
  cat << 'EOF' >> "${CONFIG_FILE}"

Host scb.c.googlers.com *.c.googlers.com
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 4h
    ServerAliveInterval 60
    ServerAliveCountMax 10
EOF
  chmod 600 "${CONFIG_FILE}"
  echo "✓ Added ControlMaster configuration to ${CONFIG_FILE}"
fi

echo "================================================================="
echo "✅ SSH ControlMaster is configured!"
echo "Next step: In your terminal, run:"
echo "   ssh scb.c.googlers.com"
echo "Once you authenticate once with your security key, all subsequent"
echo "commands from the assistant can execute through the shared socket."
echo "================================================================="
