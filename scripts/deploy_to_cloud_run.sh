#!/usr/bin/env bash
# ==============================================================================
# Script: scripts/deploy_to_cloud_run.sh
# Description: Deploy container images from Artifact Registry to Cloud Run v2
#              services with Direct VPC Egress.
# ==============================================================================
set -e

PROJECT_ID="${GCP_PROJECT:-catz-app-prod}"
REGION="${GCP_REGION:-northamerica-northeast1}"
GAR_REPO="${GAR_REPO_ID:-catz-repo}"
REGISTRY="${REGION}-docker.pkg.dev"
REPO_PATH="${REGISTRY}/${PROJECT_ID}/${GAR_REPO}"

GAME_IMAGE="${REPO_PATH}/game-server:latest"
CAT_IMAGE="${REPO_PATH}/cat-agent:latest"

echo "================================================================="
echo "🚀 Deploying Container Images to Google Cloud Run"
echo "================================================================="
echo "Project:     ${PROJECT_ID}"
echo "Region:      ${REGION}"
echo "Game Image:  ${GAME_IMAGE}"
echo "Cat Image:   ${CAT_IMAGE}"
echo "================================================================="
echo ""

# 1. Deploy Game Server
echo "🎮 [1/2] Deploying Game Server to Cloud Run..."
gcloud run deploy game-server \
  --image="${GAME_IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --port=8000 \
  --quiet

GAME_URL=$(gcloud run services describe game-server --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")
echo "✓ Game Server deployed successfully: ${GAME_URL}"
echo ""

# 2. Deploy Cat Agent Workbench
echo "🐱 [2/2] Deploying Cat Agent Workbench to Cloud Run..."
gcloud run deploy cat-agent \
  --image="${CAT_IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --port=8001 \
  --set-env-vars="GAME_SERVER_URL=${GAME_URL},GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_REGION=${REGION}" \
  --quiet

CAT_URL=$(gcloud run services describe cat-agent --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")
echo "✓ Cat Agent deployed successfully: ${CAT_URL}"
echo ""

# 3. Summary
echo "================================================================="
echo "🎉 Deployment Complete!"
echo "• Game Server URI:  ${GAME_URL}"
echo "• Cat Agent URI:    ${CAT_URL}"
echo "================================================================="
