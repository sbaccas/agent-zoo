# Infrastructure Operations & Runbook

This runbook describes operational procedures for provisioning, maintaining, deploying updates, and troubleshooting the Cat & Mouse Platform.

---

## 1. Quick Access Guide

### Accessing the Web GUI via IAP Tunnel

The Linux Jumpbox runs a containerized Chromium desktop with web rendering over port 3000. Because the jumpbox has no public IP, connect using the Google Cloud Identity-Aware Proxy (IAP) TCP tunnel:

```bash
# Terminal: Start IAP TCP tunnel to Jumpbox port 3000
gcloud compute start-iap-tunnel browser-jumpbox 3000 \
  --local-host-port=localhost:3000 \
  --zone=northamerica-northeast1-a \
  --project=catz-app-prod
```

Once running, open your local browser:
* **Jumpbox Desktop GUI:** [http://localhost:3000](http://localhost:3000)
* Inside the Chromium browser on the desktop, navigate to:
  * **Authoritative Game Server:** `http://game.catz.internal/`
  * **Cat Agent Workbench:** `http://agent.catz.internal/`

---

## 2. Infrastructure Provisioning (Terraform)

All infrastructure is defined in `infra/terraform/`:

```bash
cd infra/terraform

# Initialize Terraform providers
terraform init

# Validate configuration
terraform validate

# Plan deployment
terraform plan -var-file="terraform.tfvars"

# Apply deployment
terraform apply -var-file="terraform.tfvars" -auto-approve
```

---

## 3. Container Build & Push Pipeline

To build and push updated container images to Google Artifact Registry:

### Option A: Build on the Jumpbox VM (Recommended)

```bash
# 1. Archive local repository files
tar czf /tmp/catz_build.tar.gz Dockerfile requirements.txt app/ actors/ scripts/

# 2. Upload archive to Jumpbox via IAP SCP
gcloud compute scp /tmp/catz_build.tar.gz browser-jumpbox:/tmp/catz_build.tar.gz \
  --zone=northamerica-northeast1-a \
  --project=catz-app-prod

# 3. Trigger build & push directly inside Jumpbox
gcloud compute ssh browser-jumpbox --zone=northamerica-northeast1-a --project=catz-app-prod --command="
  set -e
  rm -rf /tmp/build && mkdir -p /tmp/build && cd /tmp/build
  tar xzf /tmp/catz_build.tar.gz
  gcloud auth configure-docker northamerica-northeast1-docker.pkg.dev --quiet
  docker build --target cat-agent -t northamerica-northeast1-docker.pkg.dev/catz-app-prod/catz-repo/cat-agent:latest .
  docker push northamerica-northeast1-docker.pkg.dev/catz-app-prod/catz-repo/cat-agent:latest
"

# 4. Deploy updated revision to Cloud Run
gcloud run deploy cat-agent \
  --image=northamerica-northeast1-docker.pkg.dev/catz-app-prod/catz-repo/cat-agent:latest \
  --region=northamerica-northeast1 \
  --project=catz-app-prod \
  --quiet
```

---

## 4. Health Checks & Diagnostic Commands

Run these commands to verify the operational state:

### 1. Check Cat Agent Live Status & WebSocket Connection
```bash
gcloud compute ssh browser-jumpbox --zone=northamerica-northeast1-a --project=catz-app-prod --command="
  curl -s http://agent.catz.internal/api/agent/server/status
"
```
**Expected Output:**
```json
{"connected": true, "server_url": "ws://game.catz.internal/ws/agents", "assigned_id": "cat-1", "last_error": null}
```

### 2. Check Game Server Status
```bash
gcloud compute ssh browser-jumpbox --zone=northamerica-northeast1-a --project=catz-app-prod --command="
  curl -s http://game.catz.internal/api/simulations/active
"
```

### 3. Check Cloud Run Logs
```bash
# View recent Cat Agent logs
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="cat-agent"' \
  --project=catz-app-prod \
  --limit=25 \
  --format="value(textPayload)"
```

---

## 5. Troubleshooting Matrix

| Symptom | Probable Root Cause | Resolution |
| :--- | :--- | :--- |
| **`HTTP 403 Forbidden` on internal URLs** | Missing or invalid OIDC Bearer token in request header | Verify `catz-gateway.service` is running on Jumpbox (`systemctl status catz-gateway`). Ensure caller fetches token targeting Cloud Run service URI. |
| **Cat Agent shows "Disconnected"** | Agent unable to authenticate WebSocket connection to Game Server | Ensure `GAME_SERVER_AUDIENCE` env var is configured in Cloud Run pointing to `https://game-server-...` and `catz-cloudrun-sa` has `roles/run.invoker`. |
| **IAP tunnel fails to connect (`port 3000`)** | Jumpbox container stopped or firewall rule missing | Verify Docker container `browser-gui` is running (`docker ps`) and firewall rule `catz-vpc-allow-iap` allows port 3000. |
| **Vertex AI calls fail (`ADC error`)** | Cloud Run missing Vertex AI role or NAT routing broken | Verify `catz-cloudrun-sa` has `roles/aiplatform.user` and `catz-vpc-nat` is active. |
