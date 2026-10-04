#!/usr/bin/env bash
# ==============================================================================
# Script: scripts/push_images_to_gar.sh
# Description: Tag and push container images built on CloudTop to Google
#              Artifact Registry (GAR).
# ==============================================================================
set -e

PROJECT_ID="${GCP_PROJECT:-catz-app-prod}"
REGION="${GCP_REGION:-northamerica-northeast1}"
GAR_REPO="${GAR_REPO_ID:-catz-repo}"
CLOUDTOP_HOST="${CLOUDTOP_HOST:-scb.c.googlers.com}"
REGISTRY="${REGION}-docker.pkg.dev"
REPO_PATH="${REGISTRY}/${PROJECT_ID}/${GAR_REPO}"

echo "================================================================="
echo "📦 Push Container Images from CloudTop to Artifact Registry (GAR)"
echo "================================================================="
echo "Target Registry: ${REPO_PATH}"
echo "CloudTop Host:   ${CLOUDTOP_HOST}"
echo ""

# 1. Obtain GCP Access Token from local gcloud
echo "🔑 Generating OAuth access token via gcloud..."
ACCESS_TOKEN=$(gcloud auth print-access-token)
if [ -z "${ACCESS_TOKEN}" ]; then
  echo "❌ Error: Failed to generate access token. Make sure you are logged in via 'gcloud auth login'."
  exit 1
fi
echo "✓ Access token generated successfully."

# 2. Authenticate Podman on CloudTop
echo ""
echo "🔐 Authenticating Podman on CloudTop to ${REGISTRY}..."
ssh "${CLOUDTOP_HOST}" "podman login -u oauth2accesstoken -p '${ACCESS_TOKEN}' ${REGISTRY}"

# 3. Tag and Push Game Server Image
GAME_REMOTE_TAG="${REPO_PATH}/game-server:latest"
echo ""
echo "🏷️ Tagging and pushing Game Server image..."
ssh "${CLOUDTOP_HOST}" "
  podman tag localhost/agent-zoo-game:latest ${GAME_REMOTE_TAG}
  echo '🚀 Pushing ${GAME_REMOTE_TAG}...'
  podman push ${GAME_REMOTE_TAG}
"
echo "✓ Game Server image pushed successfully!"

# 4. Tag and Push Cat Agent Image
CAT_REMOTE_TAG="${REPO_PATH}/cat-agent:latest"
echo ""
echo "🏷️ Tagging and pushing Cat Agent image..."
ssh "${CLOUDTOP_HOST}" "
  podman tag localhost/agent-zoo-cat:latest ${CAT_REMOTE_TAG}
  echo '🚀 Pushing ${CAT_REMOTE_TAG}...'
  podman push ${CAT_REMOTE_TAG}
"
echo "✓ Cat Agent image pushed successfully!"

# 5. Tag and Push Cat & Mouse MCP Server Image
MCP_REMOTE_TAG="${REPO_PATH}/mcp-server:latest"
echo ""
echo "🏷️ Tagging and pushing MCP Server image..."
ssh "${CLOUDTOP_HOST}" "
  podman tag localhost/agent-zoo-mcp:latest ${MCP_REMOTE_TAG}
  echo '🚀 Pushing ${MCP_REMOTE_TAG}...'
  podman push ${MCP_REMOTE_TAG}
"
echo "✓ MCP Server image pushed successfully!"

# 6. Verify images in Artifact Registry
echo ""
echo "📋 Verifying images in Artifact Registry repository '${GAR_REPO}'..."
gcloud artifacts docker images list "${REPO_PATH}" --project="${PROJECT_ID}"

echo ""
echo "================================================================="
echo "✅ All images successfully pushed to Artifact Registry!"
echo "Next step: Run ./scripts/deploy_to_cloud_run.sh to update Cloud Run."
echo "================================================================="
