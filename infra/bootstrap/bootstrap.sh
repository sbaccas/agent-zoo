#!/usr/bin/env bash
# ==============================================================================
# Script: infra/bootstrap/bootstrap.sh
# Description: Bootstraps GCP project, GCS Terraform state bucket, Service Account,
#              Terraform backend configuration, and migrates TF state to GCS.
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

PROJECT_BASE_NAME="${1:-agent-zoo-prod}"
REGION="${REGION:-northamerica-northeast1}"
ZONE="${ZONE:-northamerica-northeast1-a}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/terraform"

echo -e "${BLUE}${BOLD}"
echo "================================================================================"
echo "          🚀 AGENT ZOO: TERRAFORM BOOTSTRAP PIPELINE"
echo "================================================================================"
echo -e "${NC}"

# 1. Verify Prerequisites
if ! command -v gcloud &>/dev/null; then
  echo -e "${RED}Error: 'gcloud' CLI is not installed or not in PATH.${NC}"
  exit 1
fi

if ! command -v terraform &>/dev/null; then
  echo -e "${RED}Error: 'terraform' is not installed or not in PATH.${NC}"
  exit 1
fi

ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
if [[ -z "$ACTIVE_ACCOUNT" ]]; then
  echo -e "${RED}Error: No active gcloud account authenticated. Run 'gcloud auth login' first.${NC}"
  exit 1
fi

echo -e "${BLUE}Authenticated as:${NC} ${BOLD}${ACTIVE_ACCOUNT}${NC}"
echo -e "${BLUE}Target Region:   ${NC} ${BOLD}${REGION}${NC} (Canada Central)"
echo -e "${BLUE}Target Zone:     ${NC} ${BOLD}${ZONE}${NC}"
echo

# 2. Project Creation / Validation
PROJECT_ID="$PROJECT_BASE_NAME"
echo -e "${YELLOW}Step 1: Checking project '${PROJECT_ID}'...${NC}"

if gcloud projects describe "$PROJECT_ID" &>/dev/null; then
  echo -e "${GREEN}Project '${PROJECT_ID}' already exists.${NC}"
else
  echo -e "Attempting to create project '${PROJECT_ID}'..."
  if ! gcloud projects create "$PROJECT_ID" --name="$PROJECT_BASE_NAME"; then
    echo -e "${YELLOW}Project ID '${PROJECT_ID}' already taken globally. Generating unique suffix...${NC}"
    RANDOM_HEX=$(od -vAn -N3 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n' || echo "$RANDOM")
    PROJECT_ID="${PROJECT_BASE_NAME}-${RANDOM_HEX}"
    echo -e "${BLUE}Creating project with unique ID: '${PROJECT_ID}'...${NC}"
    gcloud projects create "$PROJECT_ID" --name="$PROJECT_BASE_NAME"
  fi
fi

# Set active project and update ADC quota project
gcloud config set project "$PROJECT_ID"
gcloud auth application-default set-quota-project "$PROJECT_ID" --quiet 2>/dev/null || true

# 3. Link Billing
echo
echo -e "${YELLOW}Step 2: Linking Billing Account...${NC}"
BILLING_ACCOUNT_ID=$(gcloud billing accounts list --filter=open=true --format="value(name)" 2>/dev/null | head -n 1 || true)
if [[ -n "$BILLING_ACCOUNT_ID" ]]; then
  echo -e "Found active billing account: ${BOLD}${BILLING_ACCOUNT_ID}${NC}"
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT_ID" || true
else
  echo -e "${YELLOW}Warning: No open billing account detected. Link manually if required.${NC}"
fi

# 4. Enable Foundational APIs for Bootstrap & Terraform
echo
echo -e "${YELLOW}Step 3: Enabling foundational APIs...${NC}"
gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  storage.googleapis.com \
  storage-component.googleapis.com \
  compute.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iap.googleapis.com \
  aiplatform.googleapis.com

# 5. Create GCS State Bucket
BUCKET_NAME="${PROJECT_ID}-tfstate"
echo
echo -e "${YELLOW}Step 4: Provisioning GCS Bucket for Terraform state: 'gs://${BUCKET_NAME}'...${NC}"
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" &>/dev/null; then
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --default-storage-class=STANDARD \
    --uniform-bucket-level-access
  echo -e "${GREEN}Created bucket gs://${BUCKET_NAME}.${NC}"
else
  echo -e "${GREEN}Bucket gs://${BUCKET_NAME} already exists.${NC}"
fi

# Enable versioning for state rollback protection
gcloud storage buckets update "gs://${BUCKET_NAME}" --versioning

# 6. Service Account for Terraform
SA_NAME="terraform-runner"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
echo
echo -e "${YELLOW}Step 5: Provisioning Terraform Service Account '${SA_EMAIL}'...${NC}"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Terraform Automation Service Account" \
    --project="$PROJECT_ID"
fi

# Grant necessary roles to Service Account
for ROLE in \
  "roles/editor" \
  "roles/resourcemanager.projectIamAdmin" \
  "roles/storage.admin" \
  "roles/artifactregistry.admin" \
  "roles/run.admin" \
  "roles/compute.networkAdmin" \
  "roles/compute.securityAdmin" \
  "roles/compute.instanceAdmin.v1" \
  "roles/iap.tunnelResourceAccessor"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" \
    --condition=None --quiet >/dev/null || true
done

# 7. Configure Terraform Backend & Variables
echo
echo -e "${YELLOW}Step 6: Writing Terraform backend and variable files...${NC}"
mkdir -p "${TF_DIR}"

cat <<EOF > "${TF_DIR}/backend.tf"
# Generated by infra/bootstrap/bootstrap.sh
terraform {
  backend "gcs" {
    bucket = "${BUCKET_NAME}"
    prefix = "terraform/state"
  }
}
EOF

cat <<EOF > "${TF_DIR}/terraform.tfvars"
project_id = "${PROJECT_ID}"
region     = "${REGION}"
zone       = "${ZONE}"
EOF

echo -e "${GREEN}Wrote backend.tf and terraform.tfvars.${NC}"

# 8. Terraform Init & Migrate State
echo
echo -e "${YELLOW}Step 7: Initializing Terraform and migrating state to GCS...${NC}"
cd "${TF_DIR}"
terraform init -migrate-state

echo
echo -e "${GREEN}${BOLD}================================================================================${NC}"
echo -e "${GREEN}${BOLD}✓ Terraform Bootstrap Complete!${NC}"
echo -e "  • Project ID:      ${BOLD}${PROJECT_ID}${NC}"
echo -e "  • Region:          ${BOLD}${REGION}${NC} (Canada Central)"
echo -e "  • State Bucket:    ${BOLD}gs://${BUCKET_NAME}${NC}"
echo -e "  • Service Account: ${BOLD}${SA_EMAIL}${NC}"
echo -e "  • Terraform Dir:   ${BOLD}${TF_DIR}${NC}"
echo -e "${GREEN}${BOLD}================================================================================${NC}"
