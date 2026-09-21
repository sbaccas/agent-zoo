#!/usr/bin/env bash
# ==============================================================================
# Script: infra/scripts/nuke_old_project.sh
# Description: Destroys/deletes old GCP project (default: proj1-487921)
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

TARGET_PROJECT="${1:-proj1-487921}"

echo -e "${RED}${BOLD}"
echo "================================================================================"
echo "                   🚨 GCP PROJECT NUKE SCRIPT 🚨"
echo "================================================================================"
echo -e "${NC}"
echo -e "${YELLOW}Target Project to be completely deleted:${NC} ${BOLD}${TARGET_PROJECT}${NC}"
echo

# 1. Verify gcloud binary
if ! command -v gcloud &>/dev/null; then
  echo -e "${RED}Error: 'gcloud' CLI is not installed or not in PATH.${NC}"
  exit 1
fi

# 2. Check active auth
ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
if [[ -z "$ACTIVE_ACCOUNT" ]]; then
  echo -e "${RED}Error: No active gcloud account authenticated. Run 'gcloud auth login' first.${NC}"
  exit 1
fi
echo -e "${BLUE}Authenticated as:${NC} ${BOLD}${ACTIVE_ACCOUNT}${NC}"

# 3. Check if project exists
echo -e "${BLUE}Checking project existence...${NC}"
if ! gcloud projects describe "$TARGET_PROJECT" &>/dev/null; then
  echo -e "${YELLOW}Warning: Project '${TARGET_PROJECT}' does not exist or you lack permission to describe it.${NC}"
  echo -e "Checking project list..."
  gcloud projects list --filter="projectId:${TARGET_PROJECT}" || true
  exit 0
fi

# 4. Inventory key resources before deletion
echo
echo -e "${YELLOW}Inspecting active resources in ${TARGET_PROJECT}...${NC}"
echo "--- Cloud Run Services ---"
gcloud run services list --project="$TARGET_PROJECT" --format="table(metadata.name,status.address.url)" 2>/dev/null || echo "None or disabled"

echo "--- Artifact Registry Repositories ---"
gcloud artifacts repositories list --project="$TARGET_PROJECT" --format="table(name,format,location)" 2>/dev/null || echo "None or disabled"

echo "--- Compute Instances ---"
gcloud compute instances list --project="$TARGET_PROJECT" --format="table(name,zone,status)" 2>/dev/null || echo "None or disabled"

echo
echo -e "${RED}${BOLD}DANGER: This action cannot be undone. Project '${TARGET_PROJECT}' will be shut down and deleted.${NC}"
echo

# 5. User Confirmation
if [[ "${2:-}" != "--force" && "${2:-}" != "-y" ]]; then
  read -r -p "Are you sure you want to PERMANENTLY NUKE '${TARGET_PROJECT}'? (type 'yes' to proceed): " CONFIRM
  if [[ "$CONFIRM" != "yes" ]]; then
    echo -e "${GREEN}Aborted. No changes were made.${NC}"
    exit 0
  fi
fi

# 6. Execute Project Deletion
echo
echo -e "${RED}Nuking project '${TARGET_PROJECT}'...${NC}"
gcloud projects delete "$TARGET_PROJECT" --quiet

# 7. Clean up local gcloud configuration if it pointed to the nuked project
CURRENT_CONFIG_PROJECT=$(gcloud config get-value project 2>/dev/null || true)
if [[ "$CURRENT_CONFIG_PROJECT" == "$TARGET_PROJECT" ]]; then
  echo -e "${YELLOW}Unsetting local gcloud default project (${TARGET_PROJECT})...${NC}"
  gcloud config unset project
fi

echo
echo -e "${GREEN}${BOLD}✓ Project '${TARGET_PROJECT}' successfully nuked!${NC}"
echo "================================================================================"
