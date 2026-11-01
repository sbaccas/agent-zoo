# 🐳 Docker & Cloud Run Deployment Playbook
**Agent Zoo (Cat & Mouse Simulation)**

This playbook records every step, configuration, command, and rationale required to containerize, test locally with Docker or Podman, push to Google Artifact Registry (GAR), and deploy to Cloud Run.

---

## 📋 Overview of Services to Containerize

The project consists of three core HTTP services:

| Service | Entrypoint | Default Port | Role | Dependencies / Credentials |
| :--- | :--- | :--- | :--- | :--- |
| **Game Server** | `app.main:app` | `8000` | Authoritative simulation clock, 2D grid world, collision, FoV raycasting, web visualizer UI | In-memory / FastAPI / websockets |
| **Cat Agent & Workbench** | `actors.cat.api:app` | `8001` | Cat tactical actor (`POST /act`), sliding trajectory memory, workbench UI | Google ADK, Gemini 2.5 Flash on Vertex AI (Google Cloud ADC) |
| **Cat & Mouse MCP Server** | `app.mcp.server:app` | `9050` | Model Context Protocol (MCP) tool server, Coach Private Notes, Live Game Chat monitor | FastAPI / SSE / JSONL |

---

## 🛠️ Phase 1: Container Tooling Prerequisites (Docker / Podman)

On Linux workstations using rootless containers, **`podman`** and **`podman-docker`** can be used interchangeably with standard Docker Engine.

`podman-docker` provides the standard `/usr/bin/docker` command aliased to Podman, allowing all standard Docker syntax (`docker build`, `docker run`, `docker push`, `docker ps`) without requiring a root daemon.

### Step 1.1: Install Podman & Docker CLI (Optional for Rootless Linux)
```bash
# Install podman and the docker CLI compatibility layer
sudo apt-get update && sudo apt-get install -y podman podman-docker

# Configure rootless subuid/subgid mapping for current user
echo "$USER:1000000:65536" | sudo tee -a /etc/subuid
echo "$USER:1000000:65536" | sudo tee -a /etc/subgid
podman system migrate

# Verify installation
docker --version
podman --version
```

---

## 🏗️ Phase 2: Dockerfile Architecture

We use a clean, multi-stage Python 3.12 slim base to keep image sizes small and builds reproducible.

### Step 2.1: Unified / Multi-Target Dockerfile (`Dockerfile`)
A single `Dockerfile` can build the Game Server, Cat Agent Workbench, or MCP Server using build targets:

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Install system dependencies if required
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ ./app/
COPY actors/ ./actors/
COPY scripts/ ./scripts/

# ----------------------------------------------------
# Target 1: Game Server
# ----------------------------------------------------
FROM base AS game-server
EXPOSE 8000
ENV PORT=8000
CMD exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}

# ----------------------------------------------------
# Target 2: Cat Agent Workbench
# ----------------------------------------------------
FROM base AS cat-agent
EXPOSE 8001
ENV PORT=8001
CMD exec python -m uvicorn actors.cat.api:app --host 0.0.0.0 --port ${PORT}
```

---

## 🧪 Phase 3: Building & Testing Images Locally

### Step 3.1: Build Images
```bash
# Build Game Server Image
docker build --target game-server -t agent-zoo-game:latest .

# Build Cat Agent Image
docker build --target cat-agent -t agent-zoo-cat:latest .
```

### Step 3.2: Create a Local Docker Bridge Network
To allow the Game Server and Cat Agent containers to communicate by hostname:
```bash
docker network create zoo-net
```

### Step 3.3: Run the Containers Locally

**1. Run Game Server:**
```bash
docker run -d \
  --name game-server \
  --network zoo-net \
  -p 8000:8000 \
  agent-zoo-game:latest
```

**2. Run Cat Agent Workbench:**
```bash
docker run -d \
  --name cat-agent \
  --network zoo-net \
  -p 8001:8001 \
  -e GAME_SERVER_URL=http://game-server:8000 \
  -e GOOGLE_CLOUD_PROJECT=your-gcp-project-id \
  -e GOOGLE_CLOUD_REGION=northamerica-northeast1 \
  -e GOOGLE_GENAI_USE_VERTEXAI=true \
  agent-zoo-cat:latest
```
When `cat-agent` starts, it automatically establishes an outbound WebSocket client to `ws://game-server:8000/ws/agents` over `zoo-net` and binds to entity `cat-1`.

### Step 3.4: Accessing Web Servers via SSH Port Forwarding (Remote Build Host)
If the containers are running on a remote Linux workstation, forward the ports through SSH:
```bash
ssh -L 8000:localhost:8000 -L 8001:localhost:8001 your-build-host.example.com
```
Then open in your local browser:
- Game Arena Visualizer: `http://localhost:8000`
- Cat Agent Workbench: `http://localhost:8001`

### Step 3.5: Troubleshooting & Inspecting Logs
```bash
# View live container logs
docker logs -f game-server
docker logs -f cat-agent

# Shell into container for debugging
docker exec -it cat-agent /bin/bash
```

---

## 📦 Phase 4: Google Artifact Registry (GAR)

### Step 4.1: Create Artifact Registry Repository (if not already existing)
```bash
gcloud artifacts repositories create agent-zoo-repo \
  --repository-format=docker \
  --location=northamerica-northeast1 \
  --description="Agent Zoo Docker repository" \
  --project=your-gcp-project-id
```

### Step 4.2: Configure Docker Authentication for GAR
```bash
gcloud auth configure-docker northamerica-northeast1-docker.pkg.dev
```

### Step 4.3: Tag and Push Images
```bash
REPO="northamerica-northeast1-docker.pkg.dev/your-gcp-project-id/agent-zoo-repo"

# Tag
docker tag agent-zoo-game:latest ${REPO}/game-server:v1
docker tag agent-zoo-cat:latest ${REPO}/cat-agent:v1

# Push
docker push ${REPO}/game-server:v1
docker push ${REPO}/cat-agent:v1
```

---

## ☁️ Phase 5: Cloud Run Deployment

### Step 5.1: Deploy Cat Agent Service
```bash
gcloud run deploy cat-agent \
  --image=${REPO}/cat-agent:v1 \
  --platform=managed \
  --region=northamerica-northeast1 \
  --project=your-gcp-project-id \
  --allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=your-gcp-project-id,GOOGLE_CLOUD_REGION=northamerica-northeast1,GOOGLE_GENAI_USE_VERTEXAI=true"
```

### Step 5.2: Deploy Game Server
Retrieve the URL of the deployed Cat Agent, then pass it to the Game Server:
```bash
CAT_SERVICE_URL=$(gcloud run services describe cat-agent --region=northamerica-northeast1 --project=your-gcp-project-id --format='value(status.url)')

gcloud run deploy game-server \
  --image=${REPO}/game-server:v1 \
  --platform=managed \
  --region=northamerica-northeast1 \
  --project=your-gcp-project-id \
  --allow-unauthenticated \
  --set-env-vars="CAT_AGENT_URL=${CAT_SERVICE_URL}/act"
```
