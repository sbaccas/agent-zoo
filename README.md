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
        Gemini["Gemini 2.5 Flash<br/>(thinking_budget: tunable, default 512)"]
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

- **Chain-of-Thought Budgeting**: Defaults to `thinking_budget=512` for the actor and `4096` for the coach. The two differ by design: the actor answers one board four times a second and pays for thought in latency, while the coach runs once per cycle off the tick loop with nothing waiting on it. Setting the actor to `0` returns it to the original reflex behaviour (~1.1s – 1.3s vs ~3.5s). Both adjustable at runtime — see below.
- **Bounded Sliding Memory**: By refreshing the ADK session per tick and formatting the previous 3 moves (`(pos, facing, context, move, reason)`), context remains capped at ~250 tokens across hundreds of simulation ticks.
- **Fail-Safe Reflex Fallback**: The fallback is evaluated per tick, not as a tripped breaker that latches. Any tick where the model overruns the deliberation timeout (default 8s, tunable) or errors is played by the deterministic in-memory reflex instead, and the next tick asks the model again. Every such tick is labelled `heuristic_fallback` and carries the reason that caused it, so a run that quietly stopped using Gemini is visible in the box score rather than hidden behind a configured model name.

---

## 🧠 Thinking Budget

How much private scratchpad the actor gets before it must commit to an action.
The default is `512`. At `0` there is none: the first token the model emits is
already part of the verdict, which is why `CatTacticalDecision` declares
`thought` before `action` — at that setting the field order is the only
scratchpad the cat has.

> [!NOTE]
> That field order was chosen *because* there was no scratchpad, and the 10/10
> vs 1/10 result behind it was measured at budget `0`. It has not been
> re-measured now that a real scratchpad is on by default. The order is retained
> on the assumption that it is still neutral-to-helpful, not on evidence.

| Where | How |
| --- | --- |
| Workbench UI | Box 2 → **Thinking Budget** slider, `Auto` toggle, and abandon timer |
| API | `GET`/`POST` `/api/agent/thinking-budget` |
| Environment | `CAT_THINKING_BUDGET`, `CAT_DELIBERATION_TIMEOUT_SEC` |

Values: `0` disables thinking, `-1` lets the model decide (up to ~8,192 tokens),
or any token count within the model's range (1–24,576 for 2.5 Flash). Only the
tactical actor is affected; the radio agent and the evaluator hold their own
independent settings, so an experiment stays scoped to the thing that plays the
game.

> [!IMPORTANT]
> The budget is coupled to the **abandon timeout**. Thinking tokens are emitted
> before the first answer token, so at ~200 tok/s the default 8s ceiling affords
> roughly 1,300 tokens of thought. A decision that overruns is discarded and the
> tick is played by the System-1 reflex — so an unaffordable budget does not
> produce a slower cat, it produces a cat that has quietly stopped using the
> model, while the box score still says Gemini was configured. Raise both
> together; the UI warns and suggests a timeout when they do not fit.

### The evaluator's dial

The coach is a different kind of thinker and gets a separate control. It runs
once every few hundred ticks, not four times a second, so tokens are cheap here
in a way they are not for the actor — but it reasons over a whole transcript
rather than a single frame, which is exactly the workload a scratchpad helps
with.

| Where | How |
| --- | --- |
| Workbench UI | Box 6 → **Coach Thinking** slider and review timer |
| API | `GET`/`POST` `/api/agent/evaluator/thinking-budget` |
| Environment | `CAT_EVAL_THINKING_BUDGET`, `CAT_EVAL_TIMEOUT_SEC` |

Defaults: budget `4096`, review timeout `60s`, clamped to `30–900s`. The
floor is not arbitrary — the coach's `get_boxscore` tool has its own 3s timeout,
and the review window has to stay an order of magnitude above it or a slow box
score would eat the entire review.

> [!CAUTION]
> The evaluator fails differently from the actor. When the actor overruns it
> loses one tick to the reflex. When the **coach** overruns, `evaluate()` falls
> back to a positional summary — bounding boxes and blind directions — and
> **publishes that over the standing orders** in `coaching_notes.md`. The cat
> then plays the next few hundred ticks on filler. Raise the budget and the
> review timer together; the UI warns when they do not fit.

### Running an A/B

Each archived game carries `run_config.json` recording the budget, the abandon
timeout, and any mid-game changes stamped with the tick they took effect on — so
a run that moved the dial cannot be mistaken for a controlled one. Per decision,
the prompt log records the budget in force and the thought tokens actually
billed, visible in the workbench's Payload inspector.

Both agents are covered, and the change log is **split by agent**, so a run that
raised the coach's budget but left the actor alone still reads as controlled
with respect to the actor. The evaluator's review timeout is recorded alongside
it under `evaluation.timeout_sec`, for the same reason the actor's is: a run
where it was set too low produces coaching notes no model wrote.

```bash
# Launch straight into a configuration, rather than remembering to move a slider
CAT_THINKING_BUDGET=2048 CAT_DELIBERATION_TIMEOUT_SEC=20 ./scripts/launch_workbench.sh

# Or hold the actor fixed and give the coach the scratchpad instead
CAT_EVAL_THINKING_BUDGET=4096 CAT_EVAL_TIMEOUT_SEC=120 ./scripts/launch_workbench.sh
```

Compare across runs with the engine mix and deliberation timings in Box 7: the
question a thinking A/B actually answers is not just "did the cat score more",
but "did the model still get to play".

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
│   ├── models.py               # Pydantic models shared across all actors
│   ├── cat/
│   │   ├── api.py              # Cat API server & Workbench service (:8001)
│   │   ├── actor.py            # Cat actor agent (Gem1) — tick-by-tick decisions
│   │   ├── heuristic.py        # Non-LLM reflex engine & fallback
│   │   ├── store.py            # On-disk transcript & coaching-notes store
│   │   ├── actor_skills.md     # Actor prompt handbook & tactical rules
│   │   ├── subagents/
│   │   │   ├── eval.py         # Evaluator (Gem2) — long-horizon advisor
│   │   │   └── eval_skills.md  # Evaluator prompt handbook (advisory role)
│   │   └── static/             # Workbench UI (interactive controls & logs)
│   └── mouse/
│       └── api.py              # Mouse brain: pure heuristic (flee/forage), no LLM
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
