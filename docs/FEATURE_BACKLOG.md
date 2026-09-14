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
- **Status**: ✅ Completed (In Review)
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
- **Target**: Autonomous Agents (`actors/cat/`, `actors/mouse/`)
- **Description**: Replace the flat 3-tick sliding window with a three-tiered cognitive memory system that balances immediate responsiveness with historical depth.
- **Key Capabilities**:
  - **Lane 1: Working Memory**: Immediate high-fidelity tactical state (current tick, visible prey, legal actions, immediate target, recent 2-3 moves) injected directly into Gemini's prompt context.
  - **Lane 2: Episodic Memory**: Temporal narrative log of significant events and tactical pursuits (*"Tick 18: Pursued mouse-1 East; target escaped through burrow at (12, 4)"*).
  - **Lane 3: Semantic Memory**: Persistent generalized domain knowledge and learned heuristics (*"Mice frequently forage around cheese clusters"*, *"Cornering prey against perimeter walls blocks lateral evasion"*).
  - **Future Roadmap**: Extensible to vector-based Retrieval-Augmented Generation (RAG) and inter-agent memory sharing.

### `AI-02`: Asynchronous Thinking & Decoupled Clock Deliberation
- **Status**: ✅ Done (Implemented Sept 12)
- **Target**: Authoritative Game Server & Agent Clients
- **Description**: Decouple agent deliberation latency from the simulation clock so the authoritative server progresses without stalling for external LLM calls, implementing RuneScape-style target-lock combat and fast spinal reflexes.
- **Delivered Capabilities**:
  - Authoritative simulation advances on a steady tick cadence without stalling for LLM round-trips.
  - Non-blocking observation dispatch over WebSockets via `send_observation` and `action_buffers`.
  - Game scheduler implements `request_action_with_budget(budget_sec=0.8)`: defaults to `stay` (hesitation) if budget elapses, preventing game freezes.
  - Cat Agent System 1/2 Architecture:
    - **System 1 (Fast Reflexes)**: Instantaneous execution of strike-zone pounces, gap-closing lunges, and RuneScape-style target-lock pursuit (`compute_reflex_decision`).
    - **System 2 (Gemini Deliberation)**: Asynchronous background macro-strategic reasoning (`_run_background_deliberation`), dynamically steering target locks and tactical directives.
  - Removed legacy phantom `HP` chip from UI and prompts, keeping data strictly authoritative.

### `AI-03`: Agent-to-Agent (A2A) Coordination & Communication
- **Status**: 💡 Future Discovery
- **Target**: Multi-Cat & Multi-Mouse Environments
- **Description**: Enable autonomous agents to broadcast signals, negotiate tactical objectives, and coordinate pack behaviors.
- **Key Capabilities**:
  - Predator pack coordination (e.g. Cat-1 flanks while Cat-2 flushes prey).
  - Prey alarm calls alerting nearby mice of approaching predators.

### `AI-04`: Trajectory History Retroactively Relabels Fallback Ticks as Gemini
- **Status**: ✅ Fixed — the reported UI defect was the visible half of a
  client/server pair, and diagnosis uncovered two further mislabels.
- **Target**: `actors/cat/static/workbench.js`, `actors/cat/api.py`, `actors/cat/actor.py`
- **Symptom**: When a Gemini request failed and the tick was served by the heuristic
  fallback, the affected rows in the trajectory history were labelled
  `Fallback (Auth Error)` as expected — but the moment credentials or connectivity
  were restored, those **same historical rows flipped to `ADK Gemini`**. Past ticks
  changed their attribution retroactively.
- **Root Causes** (four, all confirmed):
  1. **Client relabel.** The render predicate ORed the per-item source with the
     *current, global* auth flag, so a healthy auth state overrode what each tick
     actually recorded:
     ```js
     const itemIsAdk = (item.source === 'adk_gemini' || hasAuth) && !isOverride;
     //                                              ^^^^^^^^^^^ current state, not the tick's
     ```
     `hasAuth` is recomputed from `cachedState` on every render, so every past row
     was re-evaluated against the present.
  2. **Server erases evidence.** `get_agent_state` force-set
     `last_engine_source = "adk_gemini"` and cleared `last_engine_error` on *every
     poll* whenever auth looked healthy — the server-side twin of cause 1.
  3. **Over-broad auth classification.** `mark_auth_error` treated ANY ADK exception
     as a credential failure and poisoned a 5s cache. Its test included a bare
     `"token" in error_msg.lower()`, so `max output tokens exceeded` was reported
     as an expired credential.
  4. **Opposite mislabel.** The decoupled WebSocket reflex path set
     `engine = "adk_gemini"` for what is a purely local System-1 decision, crediting
     the model for ticks it never saw.
- **Data Integrity**: ✅ The on-disk transcript always held the true per-tick
  `source`; no historical data was corrupted.
- **Fix**: A single `describeSource()` helper in `workbench.js` derives the badge
  purely from the recorded source and is never passed auth state; the server stops
  rewriting `last_engine_source`; `is_auth_error()` is now narrow and separate from
  "this one call failed"; the reflex path reports `heuristic_reflex`. Each tick now
  also records a `fallback_reason` explaining *why* it fell back, which makes future
  fallbacks self-diagnosing.
- **Tests**: `tests/test_workbench_badges.py` (executes the real JS under
  JavaScriptCore, since `node` is unavailable) and `tests/test_fallback_and_reset.py`.

---

## 🎮 Game Features

### `GAME-01`: Action Durations & OSRS-Style Tick Pacing System
- **Status**: ✅ Done (Implemented Sept 12)
- **Target**: World Engine & Simulation Scheduler (`app/game/actions.py`, `app/game/scheduler.py`, `app/models/simulation.py`)
- **Description**: Replaced 1-tile/tick baseline with Old School RuneScape (OSRS) tick pacing and action animation locks.
- **Delivered Capabilities**:
  - **Differentiated Movement Pacing**:
    - **Mice**: 1 tile every 2 ticks (`move_cooldown: 2`). Fast and nimble foragers.
    - **Cats**: 1 tile every 3 ticks (`move_cooldown: 3`). Deliberate stalkers.
    - **Fastest Possible Limit**: 1 tile per tick is the absolute fastest max limit (e.g. Cat Lunge covers 2 tiles in 2 ticks = 1 tile/tick).
  - **OSRS Action Animation Lock**:
    - When an action is initiated, entity enters `action_cooldown` lock (`locked_action`).
    - During cooldown ticks, entity is locked in animation: premature inputs are rejected with `accepted: False`.
    - Once cooldown decrements to 0, the entity is unlocked and can input its next move.
  - **Configurable Hyperparameters & Live Tuning API**:
    - `SpeciesConfig` exposes `move_cooldown`, `attack_cooldown`, `lunge_cooldown`, `attack_range`, `vision_radius`, `weight`.
    - `GET /api/simulations/{id}/species`: inspect hyperparameters.
    - `PATCH /api/simulations/{id}/species/{species_key}`: live tune hyperparameters at runtime.
  - **Visualizer & Workbench Cooldown Indicators**:
    - Game visualizer displays animated dashed cooldown rings around locked actors and lock badges in HUD.
    - Cat Workbench displays `chipCooldown` (`⚡ Ready` vs `⏳ Locked (2t: east)`).

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
