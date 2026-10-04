# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

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
# Target 1: Authoritative Game Server
# ----------------------------------------------------
FROM base AS game-server
ENV PORT=8000
EXPOSE 8000
CMD exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}

# ----------------------------------------------------
# Target 2: Cat Agent Workbench & Service
# ----------------------------------------------------
FROM base AS cat-agent
ENV PORT=8001
EXPOSE 8001
CMD exec python -m uvicorn actors.cat.api:app --host 0.0.0.0 --port ${PORT}

# ----------------------------------------------------
# Target 3: Cat & Mouse MCP Server
# ----------------------------------------------------
FROM base AS mcp-server
ENV PORT=9050
EXPOSE 9050
CMD exec python -m uvicorn app.mcp.server:create_mcp_app --factory --host 0.0.0.0 --port ${PORT}
