#!/usr/bin/env bash
# ==============================================================================
# Script: infra/scripts/push_images.sh
# Description: Tags and pushes local container images to GAR
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null || echo "your-gcp-project-id")}"
REGION="${REGION:-northamerica-northeast1}"
REPO_NAME="${REPO_NAME:-catz-repo}"
TAG="${TAG:-v1}"

GAR_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"

echo -e "${BLUE}${BOLD}"
echo "================================================================================"
echo "          📦 PUSHING CONTAINER IMAGES TO ARTIFACT REGISTRY"
echo "================================================================================"
echo -e "${NC}"
echo -e "Target Project:    ${BOLD}${PROJECT_ID}${NC}"
echo -e "Target Registry:   ${BOLD}${GAR_BASE}${NC}"
echo -e "Image Tag:         ${BOLD}${TAG}${NC}"
echo

# 1. Configure auth
echo -e "${YELLOW}Step 1: Authenticating Docker / Podman to GAR...${NC}"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# 2. Tag images
echo
echo -e "${YELLOW}Step 2: Tagging local images...${NC}"
docker tag localhost/agent-zoo-game:latest "${GAR_BASE}/game-server:${TAG}"
docker tag localhost/agent-zoo-cat:latest "${GAR_BASE}/cat-agent:${TAG}"
echo -e "${GREEN}Tagged:${NC} ${GAR_BASE}/game-server:${TAG}"
echo -e "${GREEN}Tagged:${NC} ${GAR_BASE}/cat-agent:${TAG}"

# 3. Push images
echo
echo -e "${YELLOW}Step 3: Pushing Game Server image...${NC}"
docker push "${GAR_BASE}/game-server:${TAG}"

echo
echo -e "${YELLOW}Step 4: Pushing Cat Agent image...${NC}"
docker push "${GAR_BASE}/cat-agent:${TAG}"

echo
echo -e "${GREEN}${BOLD}================================================================================${NC}"
echo -e "${GREEN}${BOLD}✓ Both images pushed to Artifact Registry!${NC}"
echo -e "  • Game Server: ${BOLD}${GAR_BASE}/game-server:${TAG}${NC}"
echo -e "  • Cat Agent:   ${BOLD}${GAR_BASE}/cat-agent:${TAG}${NC}"
echo -e "${GREEN}${BOLD}================================================================================${NC}"
