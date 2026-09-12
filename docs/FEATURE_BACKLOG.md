# 📌 Feature Backlog — Agent Zoo (Cat & Mouse)

A living backlog tracking architectural initiatives, gameplay mechanics, and agent capabilities across the **Agent Zoo Playground**.

---

## 🗂️ Categories Overview

* 🌐 **Web Features**: Networking, WebSockets, client-server protocols, visualization, and workbench UI.
* 🧠 **AI Features**: Cognitive architectures, memory systems, prompt engineering, ADK integrations, and agent-to-agent (A2A) protocols.
* 🎮 **Game Features**: World mechanics, physics, simulation clock, environmental elements, and biological/metabolic systems.

---

## 🌐 Web Features

### `WEB-01`: WebSocket Duplex Communication Architecture
- **Status**: 📋 Backlog (High Priority)
- **Target**: Game Server & Agent Clients
- **Description**: Invert connection polarity so agent clients (such as the Cat Agent) connect outbound to the Game Server via WebSockets (`ws://127.0.0.1:8000/ws/agent`) rather than the Game Server making inbound HTTP calls to the agent.
- **Key Capabilities**:
  - **Dynamic Handshake**: Agent initiates connection with metadata (`{ "type": "register", "role": "cat", "agent_name": "gemini-cat" }`).
  - **Entity Assignment**: Game server validates availability, binds the socket to an entity (`cat-1`), and returns arena dimensions.
  - **Duplex Messaging**: Server pushes `observation` payloads each tick; agent streams back `action` decisions.
  - **Instant Disconnection Detection**: Immediate cleanup or fallback to baseline heuristics when a socket closes, removing HTTP timeouts.
  - **Firewall & Cloud-Ready**: Operates without open ingress ports or reverse proxies on agent machines, preparing for Cloud Run deployments.

### `WEB-02`: Duration-Aware Visualizer & Animation Windows
- **Status**: 📋 Backlog (Medium Priority)
- **Target**: Board UI (`:8000`) & Cat Workbench (`:8001`)
- **Description**: Upgrade the simulation visualizer and workbench UI to render visual states for actions that take multiple ticks.
- **Key Capabilities**:
  - Visual windup and slash animations for heavy attacks.
  - Leap/burst trail animations for lunges.
  - Visual recovery/cooldown indicator (e.g., recovery badge or cooldown ring) showing when an actor is locked.
  - Obstacle rendering for new environmental structures (trees, stones, buildings).

---

## 🧠 AI Features

### `AI-01`: Three-Laned Cognitive Memory Architecture
- **Status**: 📋 Backlog (High Priority)
- **Target**: Autonomous Agents (`actors/cat_agent.py`, `actors/mouse_agent.py`)
- **Description**: Replace the flat 3-tick sliding window with a three-tiered cognitive memory system that balances immediate responsiveness with historical depth.
- **Key Capabilities**:
  - **Lane 1: Working Memory**: Immediate high-fidelity tactical state (current tick, visible prey, legal actions, immediate target, recent 2-3 moves) injected directly into Gemini's prompt context.
  - **Lane 2: Episodic Memory**: Temporal narrative log of significant events and tactical pursuits (*"Tick 18: Pursued mouse-1 East; target escaped through burrow at (12, 4)"*).
  - **Lane 3: Semantic Memory**: Persistent generalized domain knowledge and learned heuristics (*"Mice frequently forage around cheese clusters"*, *"Cornering prey against perimeter walls blocks lateral evasion"*).
  - **Future Roadmap**: Extensible to vector-based Retrieval-Augmented Generation (RAG) and inter-agent memory sharing.

### `AI-02`: Asynchronous Thinking & Decoupled Clock Deliberation
- **Status**: 📋 Backlog (High Priority)
- **Target**: Authoritative Game Server & Agent Clients
- **Description**: Decouple agent deliberation latency from the simulation clock so the authoritative server progresses without stalling for external LLM calls.
- **Key Capabilities**:
  - The authoritative simulation advances on a steady tick cadence.
  - Observations are dispatched asynchronously to agents over WebSockets.
  - If Gemini completes its decision (~1.2s) before the tick window closes, the action is queued and resolved at the tick barrier.
  - If deliberation exceeds the tick budget, the server automatically defaults the agent's turn to `stay` (hesitation/thinking in place) without halting other entities or freezing the world.

### `AI-03`: Agent-to-Agent (A2A) Coordination & Communication
- **Status**: 💡 Future Discovery
- **Target**: Multi-Cat & Multi-Mouse Environments
- **Description**: Enable autonomous agents to broadcast signals, negotiate tactical objectives, and coordinate pack behaviors.
- **Key Capabilities**:
  - Predator pack coordination (e.g. Cat-1 flanks while Cat-2 flushes prey).
  - Prey alarm calls alerting nearby mice of approaching predators.

---

## 🎮 Game Features

### `GAME-01`: Action Durations & Tick Cost Economics
- **Status**: 📋 Backlog (High Priority)
- **Target**: World Engine & Rules (`app/game/rules.py`, `app/game/world.py`)
- **Description**: Generalize atomic 1-tick actions into duration-based commitments with distinct tick costs and recovery locks.
- **Action Economics**:
  - `stay`: 1 tick.
  - Standard Step (`north`, `south`, `east`, `west`): 1 tick.
  - `lunge`: 2 ticks (high-velocity 2-tile burst; 1 tick recovery).
  - `attack` / `slash`: 3 ticks (1 tick windup/strike, 2 ticks recovery lock).
- **Tactical Impact**: Missed attacks create tactical openings for prey evasion. Entities have an `action_lock` / `busy_until_tick` state preventing overlapping actions.

### `GAME-02`: Calorie & Metabolic Pressure System
- **Status**: 📋 Backlog (Medium Priority)
- **Target**: Entity State & Rules (`app/models/`, `app/game/rules.py`)
- **Description**: Introduce biological constraints and metabolic costs to impose environmental pressures on predators (and prey), driving goal-directed hunting urgency.
- **Key Capabilities**:
  - **Calorie Pool**: Cats spawn with a base energy reserve (e.g., 1000 kcal).
  - **Metabolic Burn**:
    - Basal Metabolic Rate (BMR): Passive calorie burn every tick (e.g., -1 kcal/tick).
    - Action Expenditure: High-energy moves cost additional calories (e.g., step: -2 kcal, lunge: -10 kcal, slash: -15 kcal).
  - **Replenishment**: Successfully catching and consuming a mouse restores a significant calorie chunk (+400 kcal).
  - **Starvation & Exhaustion**: Low calories induce exhaustion penalties (reduced speed, disabled lunge/slash), and zero calories leads to starvation/elimination.

### `GAME-03`: Environmental Cover & Obstacles (Buildings, Trees, Stones)
- **Status**: 📋 Backlog (Medium Priority)
- **Target**: World Grid & FoV Raycasting (`app/game/world.py`, `app/models/entity.py`)
- **Description**: Enrich arena geometry with varied environmental structures and obstacles that provide tactical concealment and dynamic cover.
- **Key Capabilities**:
  - **Buildings**: Multi-tile structures with walls and doorways/openings creating indoor rooms and choke points.
  - **Stones**: Solid, impenetrable small obstacles (1x1 or 2x2) that block movement and occlude line of sight.
  - **Trees / Foliage**: Unique obstacle type:
    - May block line of sight (visual concealment for mice to hide behind).
    - Can potentially be entered or navigated by agile mice while slowing or blocking cats.
  - **Line-of-Sight & Raycasting**: The 90° vision cone raycaster respects new obstacle bounds, enabling mice to break line of sight and escape predatory pursuit.
