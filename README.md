# 🐾 Agent Zoo Playground (Cat & Mouse)

An experimental multi-agent simulation playground where autonomous animal agents (predators and prey) perceive, deliberate, compete, and interact within a discrete 2D grid world.

The project explores the spectrum of agent autonomy—ranging from high-speed rule-based heuristics and human manual steering to autonomous LLM cognitive agents built with **Google Agent Development Kit (ADK)** and **Gemini 2.5 Flash** on **Google Cloud Vertex AI**, with future foundations for Agent-to-Agent (A2A) communication.

---

## 🎯 Vision & Core Principles

1. **Authoritative World, Decoupled Brains**:
   - The **Game Server** owns ground-truth world state, physics, collision detection, vision/occlusion cones, and the simulation clock.
   - **Agents** are isolated external actors communicating strictly over a lightweight HTTP protocol (`POST /act`). Any service or language can implement an agent.

2. **The Autonomy Spectrum**:
   - **Heuristic**: In-memory algorithmic logic resolving decisions in microseconds (**0.008ms**) to sustain 60 FPS simulations.
   - **Human Override**: Interactive manual control and real-time intervention through dedicated agent workbenches.
   - **Autonomous LLM**: Cognitive reasoning via Google ADK + Gemini 2.5 Flash with structured prompt handbooks, tactical memories, and strategic intent.
   - **Future A2A (Agent-to-Agent)**: Predator coordination, prey warning calls, and negotiation protocols across independent agents.

3. **Production-Grade Resilience**:
   - **Sliding Context Window**: Compact 3-tick trajectory history prevents context explosion and keeps prompt latency flat (~1.2s).
   - **Circuit Breaker**: 3.0s agent request timeout with automatic fallback to in-memory heuristics if an agent service degrades or disconnects.
   - **Zero-LLM UI Polling**: Workbench and visualization polling operate strictly against in-memory state caches (<1ms), preventing browser polling from inducing cloud API overhead.

---

## 🏗️ System Architecture

```mermaid
flowchart TB
    subgraph ClientLayer["User Interfaces"]
        BoardUI["Simulation Control Panel<br/>(Port 8000)"]
        CatWorkbench["Cat Agent Workbench<br/>(Port 8001)"]
    end

    subgraph ServerLayer["Game Server (:8000)"]
        FastAPIServer["FastAPI Engine"]
        Clock["Simulation Clock & Scheduler"]
        World["2D Grid World & FoV Engine"]
        CircuitBreaker["Circuit Breaker & Fallback"]
    end

    subgraph AgentLayer["Cat Agent Service (:8001)"]
        AgentAPI["FastAPI Receiver (/act)"]
        Memory["3-Tick Sliding Trajectory Cache"]
        Handbook["Tactical Skills Handbook (skills.md)"]
        ADKRunner["Google ADK LlmAgent & Runner"]
    end

    subgraph CloudLayer["Google Cloud Vertex AI"]
        Gemini["Gemini 2.5 Flash<br/>(thinking_budget=0)"]
    end

    BoardUI -->|HTTP / WebSocket| FastAPIServer
    CatWorkbench -->|GET /api/agent/state| AgentAPI
    FastAPIServer --> Clock --> World
    World -->|POST /act (Observation)| CircuitBreaker
    CircuitBreaker -->|HTTP Request| AgentAPI
    AgentAPI --> Memory --> ADKRunner
    Handbook --> ADKRunner
    ADKRunner -->|gcloud ADC| Gemini
    Gemini -->|Action & Thought| ADKRunner
    ADKRunner -->|HTTP 200 Decision| FastAPIServer
```

---

## 🔄 Agent HTTP Protocol

Any agent connects to the simulation by implementing the `/act` endpoint:

### 1. Inbound Observation (`POST /act`)
```json
{
  "tick": 42,
  "round": 1,
  "agent_id": "cat-1",
  "position": {"x": 10, "y": 8},
  "facing": "east",
  "hp": 100,
  "legal_actions": ["stay", "north", "south", "east", "west", "lunge", "attack"],
  "visible_entities": [
    {"id": "mouse-1", "type": "mouse", "position": {"x": 12, "y": 8}}
  ]
}
```

### 2. Outbound Tactical Decision
```json
{
  "action": "lunge",
  "thought": "Target mouse-1 detected 2 tiles ahead down open corridor. Executing lunge to close gap.",
  "target_mouse_id": "mouse-1",
  "strategy": "LUNGE"
}
```

---

## ⚡ Performance & Optimization Highlights

- **Chain-of-Thought Budgeting**: Configured with `thinking_budget=0`, reducing Gemini 2.5 Flash response times from ~3.5s down to **~1.1s – 1.3s**.
- **Bounded Sliding Memory**: By refreshing the ADK session per tick and formatting the previous 3 moves (`(pos, facing, context, move, reason)`), context remains capped at ~250 tokens across hundreds of simulation ticks.
- **Fail-Safe Heuristic Fallback**: If the Cat Agent takes longer than 3.0s or fails twice consecutively, the scheduler trips a circuit breaker and executes pure in-memory pursuit (0.008ms) until the service stabilizes.

---

## 📂 Project Structure

```text
agent-zoo/
├── app/                        # Authoritative Game Server
│   ├── main.py                 # FastAPI application entrypoint
│   ├── game/                   # World engine, scheduler, rules & FoV
│   │   ├── scheduler.py        # Simulation clock & HTTP agent dispatcher
│   │   ├── world.py            # Discrete 2D grid, collision & raycasting
│   │   └── rules.py            # Combat, lunge mechanics & win conditions
│   ├── api/                    # REST endpoints & control interfaces
│   ├── models/                 # Pydantic domain models
│   └── static/                 # Board visualizer & simulation control panel
│
├── actors/                     # Autonomous Agents & Workbenches
│   ├── cat_agent.py            # Google ADK Cat Agent & Workbench service (:8001)
│   ├── mouse_agent.py          # Mouse agent heuristics & behaviors
│   ├── skills.md               # Prompt engineering handbook & tactical rules
│   └── static/                 # Workbench UI (interactive controls & logs)
│
├── scripts/
│   └── launch_workbench.sh     # Preflight check (ADC token) & workbench launcher
│
├── tests/                      # Pytest test suite (35+ unit & integration tests)
├── requirements.txt            # Python dependencies (FastAPI, ADK, Google GenAI)
├── .env.example                # Example environment template
└── README.md                   # Project documentation
```

---

## 🚀 Getting Started

### Prerequisites
- **Python 3.12+**
- **Google Cloud SDK (`gcloud`)** with access to Vertex AI (Project: `proj1-487921`, Region: `northamerica-northeast1`)

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/sbaccas/agent-zoo.git
cd agent-zoo

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Authentication (Google Cloud ADC)

Authenticate with your Google Cloud project using Application Default Credentials:

```bash
gcloud auth application-default login --no-launch-browser
```

Create your local `.env` file (copied from `.env.example`):
```bash
cp .env.example .env
```

Ensure the following variables are present:
```ini
GOOGLE_CLOUD_PROJECT=proj1-487921
GOOGLE_CLOUD_REGION=northamerica-northeast1
GOOGLE_GENAI_USE_VERTEXAI=true
CAT_AGENT_MODE=gemini
```

---

## 🎮 Running the Simulation

Running the full interactive loop requires two terminal processes:

### Terminal 1: Game Server (Port 8000)
```bash
source .venv/bin/activate
uvicorn app.main:app --port 8000 --reload
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser to view the authoritative arena, add/remove animals, adjust simulation speed, or switch control modes (`auto`, `manual`, `http`).

### Terminal 2: Cat Agent Workbench (Port 8001)
```bash
./scripts/launch_workbench.sh
```
Open **[http://127.0.0.1:8001](http://127.0.0.1:8001)** in your browser to inspect the Cat's cognitive status, live FoV vision cone, historical reasoning trail, and toggle between autonomous Gemini 2.5 Flash decisions and manual action overrides.

---

## 🧪 Testing & Verification

Run the full pytest suite:

```bash
.venv/bin/pytest -v
```

All 35 unit and integration tests cover:
- World coordinate system, wall collisions, and 90° FoV raycasting.
- Simulation turn scheduling and HTTP circuit breaker behavior.
- Agent workbench endpoints, observation ingestion, and response generation.

---

## 🗺️ Roadmap & Next Horizons

- [x] **v0.1**: Authoritative discrete-grid game server with web visualizer.
- [x] **v0.2**: Google ADK + Gemini 2.5 Flash integration on Vertex AI with ~1.2s turn latency.
- [x] **v0.3**: Dedicated Cat Agent Workbench with zero-LLM polling overhead and manual steering.
- [ ] **v0.4**: Autonomous Mouse LLM agent with evasion tactics and burrow concealment.
- [ ] **v0.5**: Multi-agent predator/prey coordination with Agent-to-Agent (A2A) protocol.
- [ ] **v1.0**: Cloud Run containerized deployment with multi-room competitive arenas.
