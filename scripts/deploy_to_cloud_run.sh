#!/usr/bin/env bash
# ==============================================================================
# Script: scripts/deploy_to_cloud_run.sh
# Description: Deploy container images from Artifact Registry to Cloud Run v2
#              services with Direct VPC Egress.
# ==============================================================================
set -e

PROJECT_ID="${GCP_PROJECT:-your-gcp-project-id}"
REGION="${GCP_REGION:-northamerica-northeast1}"
GAR_REPO="${GAR_REPO_ID:-agent-zoo-repo}"
REGISTRY="${REGION}-docker.pkg.dev"
REPO_PATH="${REGISTRY}/${PROJECT_ID}/${GAR_REPO}"

GAME_IMAGE="${REPO_PATH}/game-server:latest"
CAT_IMAGE="${REPO_PATH}/cat-agent:latest"
MCP_IMAGE="${REPO_PATH}/mcp-server:latest"

echo "================================================================="
echo "🚀 Deploying Container Images to Google Cloud Run"
echo "================================================================="
echo "Project:     ${PROJECT_ID}"
echo "Region:      ${REGION}"
echo "Game Image:  ${GAME_IMAGE}"
echo "Cat Image:   ${CAT_IMAGE}"
echo "MCP Image:   ${MCP_IMAGE}"
echo "================================================================="
echo ""

# 1. Deploy Game Server
echo "🎮 [1/4] Deploying Game Server to Cloud Run..."
gcloud run deploy game-server \
  --image="${GAME_IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --port=8000 \
  --quiet

GAME_URL=$(gcloud run services describe game-server --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")
echo "✓ Game Server deployed successfully: ${GAME_URL}"
echo ""

# 2. Deploy Cat & Mouse MCP Server
echo "🔌 [2/4] Deploying Cat & Mouse MCP Server to Cloud Run..."
gcloud run deploy mcp-server \
  --image="${MCP_IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --port=9050 \
  --set-env-vars="CAT_MCP_PORT=9050,CAT_MCP_HOST=0.0.0.0,GAME_SERVER_URL=${GAME_URL}" \
  --quiet

MCP_URL=$(gcloud run services describe mcp-server --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")
echo "✓ MCP Server deployed successfully: ${MCP_URL}"
echo ""

# 3. Deploy Cat Agent 1 Workbench
echo "🐱 [3/4] Deploying Cat Agent 1 Workbench to Cloud Run..."
gcloud run deploy cat-agent \
  --image="${CAT_IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --port=8001 \
  --set-env-vars="AGENT_ID=cat-1,GAME_SERVER_URL=${GAME_URL},GAME_SERVER_AUDIENCE=${GAME_URL},CAT_MCP_SERVER_URL=http://mcp.catz.internal,MCP_SERVER_AUDIENCE=${MCP_URL},GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION}" \
  --quiet

CAT_URL=$(gcloud run services describe cat-agent --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")
echo "✓ Cat Agent 1 deployed successfully: ${CAT_URL}"
echo ""

# 4. Deploy Cat Agent 2 Workbench
echo "🐱 [4/4] Deploying Cat Agent 2 Workbench to Cloud Run..."
gcloud run deploy cat-agent-2 \
  --image="${CAT_IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --port=8003 \
  --set-env-vars="AGENT_ID=cat-2,GAME_SERVER_URL=${GAME_URL},GAME_SERVER_AUDIENCE=${GAME_URL},CAT_MCP_SERVER_URL=http://mcp.catz.internal,MCP_SERVER_AUDIENCE=${MCP_URL},GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION}" \
  --quiet

CAT2_URL=$(gcloud run services describe cat-agent-2 --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")
echo "✓ Cat Agent 2 deployed successfully: ${CAT2_URL}"
echo ""

# 5. Summary
echo "================================================================="
echo "🎉 Deployment Complete!"
echo "• Game Server URI:  ${GAME_URL}"
echo "• Cat Agent 1 URI:  ${CAT_URL}"
echo "• Cat Agent 2 URI:  ${CAT2_URL}"
echo "• MCP Server URI:   ${MCP_URL}"
echo "================================================================="
