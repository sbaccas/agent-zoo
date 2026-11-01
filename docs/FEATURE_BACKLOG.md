# 📌 Feature Backlog — Agent Zoo (Cat & Mouse)

A living backlog tracking architectural initiatives, gameplay mechanics, and agent capabilities across the **Agent Zoo Playground**.

---

## 🗂️ Categories Overview

* 🌐 **Web Features**: Networking, WebSockets, client-server protocols, visualization, and workbench UI.
* 🧠 **AI Features**: Cognitive architectures, memory systems, prompt engineering, ADK integrations, and agent-to-agent (A2A) protocols.
* 🎮 **Game Features**: World mechanics, physics, simulation clock, environmental elements, and biological/metabolic systems.
* 🚀 **DevOps & Cloud Features**: Cloud Run containerization, Terraform IaC, CI/CD pipelines, and multi-tenant cloud orchestration.

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
- **Status**: ✅ Done (Implemented Sept 12, superseded Sept 18 — see `AI-02b`)
- **Target**: Authoritative Game Server & Agent Clients
- **Description**: Decouple agent deliberation latency from the simulation clock so the authoritative server progresses without stalling for external LLM calls, implementing RuneScape-style target-lock combat and fast spinal reflexes.
- **Delivered Capabilities**:
  - Authoritative simulation advances on a steady tick cadence without stalling for LLM round-trips.
  - Non-blocking observation dispatch over WebSockets via `send_observation` and `action_buffers`.
  - Game scheduler implemented `request_action_with_budget(budget_sec=0.8)`: defaulted to `stay` (hesitation) if the budget elapsed, preventing game freezes.
  - Cat Agent System 1/2 Architecture:
    - **System 1 (Fast Reflexes)**: Instantaneous execution of strike-zone pounces, gap-closing lunges, and RuneScape-style target-lock pursuit (`compute_reflex_decision`).
    - **System 2 (Gemini Deliberation)**: Asynchronous background macro-strategic reasoning (`_run_background_deliberation`), dynamically steering target locks and tactical directives.
  - Removed legacy phantom `HP` chip from UI and prompts, keeping data strictly authoritative.

### `AI-02b`: Fully Asynchronous Dispatch / Collect
- **Status**: ✅ Done (Implemented Sept 18)
- **Target**: Authoritative Game Server & Agent Clients
- **Description**: Remove the last place the simulation waited on an agent, and bound how stale an accepted answer may be.
- **Why `AI-02` needed replacing**:
  - `request_action_with_budget` still blocked the tick for up to `budget_sec` *per agent, serially*, so the real tick period was the configured one plus every agent's latency, and N agents divided one window instead of sharing it.
  - Its `stay` default is a legal action and therefore truthy, so the heuristic fallback sitting underneath it was unreachable: a WebSocket cat that missed its window stood still rather than being covered.
  - The action buffer was unstamped, so staleness was unbounded. A reply computed several ticks earlier executed exactly like a fresh one.
- **Delivered Capabilities**:
  - Observations are dispatched once per tick, after the board is final, to every connected agent at once (`Scheduler._dispatch_observations`). Nothing is awaited on an agent's behalf.
  - Answers are taken at the start of the following tick (`WsAgentManager.collect_action`) and validated against `last_dispatched_tick`. A mismatched answer is discarded as stale.
  - A missed or stale answer hands the turn to the species heuristic.
  - The deliberation budget travels to the agent as a *duration* (`budget_sec`), derived from `tick_rate` so the promise cannot drift from the enforced interval.
  - The agent deliberates in a cancellable task rather than inline, so a slow answer no longer blocks its own receive loop and push it further behind each tick. A newer observation supersedes an older deliberation.
  - Late and failed deliberations are abandoned silently rather than answered with `stay`; the heuristic plays those turns instead.
  - Box score reports `decisions_by_model`, `decisions_by_fallback`, `decisions_stale` and `model_control_pct`, so the heuristic's strong play cannot be misread as the model's.
  - Agent status reports deliberation latency and outcome counts, including abandoned attempts.

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

### `AI-05`: Sticky Movesets — Persistent Standing Orders Instead of Per-Tick Choices
- **Status**: 📋 Backlog (High Priority)
- **Target**: `actors/cat/api.py`, `actors/cat/heuristic.py`
- **Problem**: The Cat re-decides from scratch every tick, but Gemini can only answer
  about one tick in eight (see `AI-06`). The model's intent is therefore discarded
  almost immediately: it says "go west", the reflex overrides the next seven ticks,
  and the decision never compounds into a trajectory.
- **Concept**: Model the Cat less like a turn-based actor and more like a vehicle that
  does not stop. A Gemini decision sets a **standing heading** that persists across
  every subsequent tick until Gemini issues a different one. The model steers; it does
  not need to be present for every tick to stay in control.
- **Key Capabilities**:
  - A durable `current_moveset` (heading / intent) on the agent state, set only by the
    deliberation path and never cleared by the reflex.
  - The reflex demotes from *decision-maker* to *executor*: it continues the standing
    order and handles only genuinely reflexive interrupts (mouse in the strike zone, a
    wall directly ahead) rather than inventing its own roaming direction.
  - Interrupt precedence needs defining: which events may override a standing order,
    and whether an interrupt ends the moveset or suspends and resumes it.
  - Staleness policy: whether a moveset expires after N ticks or persists until replaced.
- **Why it matters**: This is what makes the coaching dossier reachable. Directives
  like "sweep east along row 12" are trajectory-shaped, and a trajectory cannot be
  expressed by an agent that only controls one tick in eight.
- **Open questions**: Does a moveset survive a round reset? Should the evaluator be
  able to set one directly, or only influence the model that sets it?

### `AI-06`: Reflex Heuristic Decides the Overwhelming Majority of Ticks
- **Status**: 🩹 Mitigated (tick-rate stopgap shipped); root fix tracked by `AI-05`
- **Target**: `actors/cat/api.py`, `app/static/index.html`
- **Measured**: Over 755 recorded ticks the decision source split was
  `heuristic_reflex` **86.1%** vs `adk_gemini` **13.9%**. The dominant
  `fallback_reason` was *"Gemini still deliberating; acted on System-1 reflex within
  tick budget"* — never auth, never an illegal action.
- **Cause**: A race, not a failure. `act_async` consumes a completed background
  deliberation if one is ready and otherwise fires the reflex immediately. At the
  default 5 ticks/sec the budget is 200ms per tick while Gemini takes 1.6–2.2s, so the
  model is lapped roughly eight times over.
- **Consequence**: The evaluator's coaching notes only influence the ~14% of ticks
  Gemini decides. Worse, the evaluator has been writing directives aimed at behaviour
  the model does not control — *"Do not revert to exploring territory"* targets the
  string literal in `heuristic.compute_reflex_decision`'s roaming branch, which cannot
  read coaching notes at all.
- **Mitigation shipped**: Sub-1 tick rates (`0.5` = 1 tick / 2s, `0.25` = 1 tick / 4s)
  added to the Execution Speed dropdown, giving Gemini a budget longer than its
  latency. Also fixed the dropdown itself, which was write-only and displayed a
  hardcoded default rather than the simulation's real rate.
- **Remaining work**: The reflex's attack rung fires whenever a mouse is within
  `abs(offaxis) <= 1`, which includes diagonals, so it swings and misses — precisely
  the alignment problem the evaluator keeps flagging. Tightening that rung is
  independent of `AI-05` and worth doing on its own.
- **Tests**: `tests/test_tick_rate_control.py`.

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

### `GAME-04`: Cheese Refrigerators, POI Attractors & Respawning Prey Clusters
- **Status**: 📋 Backlog (High Priority)
- **Target**: World Entities, Prey AI & Game Server (`app/game/`, `actors/mouse/`)
- **Description**: Introduce interactive Points of Interest (POIs) across the arena to create organic hunting grounds and micro-ecosystems.
- **Key Capabilities**:
  - **Refrigerator / Cheese Landmark (POI)**:
    - Stationary interactive fixtures placed across the grid (e.g. at fixed or configurable coordinates).
    - Distinct visual representation on canvas (refrigerator / cheese crate icon).
    - Scent attractor radius (e.g. 8–10 tiles) drawing foraging mice.
  - **Tethered Prey / Cheese Swarms**:
    - Dedicated cohort of mice tethered to specific refrigerator POIs with localized foraging ranges (3–5 tile orbit).
    - Instead of purely random wandering, they prioritize staying near or returning to the cheese when not fleeing predators.
  - **POI Prey Respawning**:
    - When a cheese-tethered mouse is captured/killed by a cat, the refrigerator schedules a respawn event after $N$ ticks (e.g., 15–30 ticks).
    - Sustains permanent focal points for predatory strategy, rewarding cats that establish perimeter patrols or ambush routes.

---

## 🚀 DevOps & Cloud Features

### `DEVOPS-01`: Cloud Infrastructure & Terraform (GCP / Cloud Run)
- **Status**: 📋 Backlog (High Priority)
- **Target**: Infrastructure as Code (`infra/terraform/`)
- **Description**: Provision and manage cloud infrastructure on Google Cloud Platform using Terraform.
- **Key Capabilities**:
  - **Project & Provider Configuration**:
    - Target GCP Project (`your-gcp-project-id`), Region (`northamerica-northeast1`).
    - Remote state backend (GCS bucket).
  - **Artifact Registry**:
    - Dedicated Docker registry for containerized `game-server` and `cat-agent` images.
  - **Cloud Run Deployment**:
    - `game-server`: WebSockets enabled (`sessionAffinity = true`), HTTP/2, CPU always allocated (for consistent simulation clock), ingress settings.
    - `cat-agent-workbench`: Private/public ingress with token auth, bound to Vertex AI service account.
  - **IAM & Security**:
    - Dedicated Service Accounts with least-privilege roles (`roles/aiplatform.user` for Vertex AI Gemini calls).
    - Service-to-service IAM bindings between Game Server and Cat Agent.

### `DEVOPS-02`: CI/CD Pipelines (Automated Testing, Docker Builds & Deployment)
- **Status**: 📋 Backlog (High Priority)
- **Target**: GitHub Actions / Cloud Build (`.github/workflows/`)
- **Description**: Fully automated continuous integration and continuous deployment pipelines.
- **Key Capabilities**:
  - **CI (Quality & Regressions)**:
    - Automated linting and full pytest execution (`.venv/bin/pytest tests/`) on pull requests and branch pushes.
  - **Container Build & Security Scanning**:
    - Multi-stage Dockerfile builds optimizing layer caching and minimizing image sizes.
    - Vulnerability and dependency vulnerability scanning.
  - **Automated CD**:
    - Automated deployment to Cloud Run staging/production environments upon merging to `main` or creating release tags.

---

## ⚡ October 2026 — Gameplay & LLM Optimization Board

### `GAME-05`: `cat-3` (Evil Cat) Score & Kill Accounting Bug
- **Status**: 📋 Backlog (High Priority)
- **Target**: [`app/game/scheduler.py`](app/game/scheduler.py#L304-L336), [`app/game/stats.py`](app/game/stats.py#L273-L337)
- **Description**: Resolve discrepancy where `cat-3` increments `stats.kills += 1` when eliminating a rival cat (`stats.py:275`), while `scheduler.py:323` sets `earned_score = absorbed_score` (without `+1` for the cat takedown itself) and `stats.py:336` computes `cat_score = s.absorbed_kills` (ignoring `s.kills`).

### `GAME-06`: HP, Damage & Healing Balance Optimization
- **Status**: 📋 Backlog (High Priority)
- **Target**: [`app/game/entities.py`](app/game/entities.py), [`app/game/ws_manager.py`](app/game/ws_manager.py#L120-L126), [`app/game/scheduler.py`](app/game/scheduler.py#L340-L348)
- **Description**: Audit and tune HP pools (`100` regular cat vs. `150` Evil Cat), slash damage (`34`), and predation healing (`+40 HP` mouse capture, `+100 HP` cat elimination).

### `OPT-05`: Eliminate Double PNG Viewport Render in `cat-3` Actor
- **Status**: ✅ Done
- **Target**: [`actors/evil_cat/actor.py`](actors/evil_cat/actor.py#L627-L715)
- **Description**: Cache `_viewport_png_bytes` inside `capture` during `build_decision_prompt` and reuse those bytes directly in `query_adk_gemini` instead of rendering the 260×260 PIL PNG a second time on every `cat-3` tick.

### `OPT-03`: Deduplicate Triplicated History in `cat-3` Coach Prompt (`build_coach_eval_prompt`)
- **Status**: ✅ Done
- **Target**: [`actors/evil_cat/eval.py`](actors/evil_cat/eval.py#L945-L1085), [`actors/evil_cat/actor.py`](actors/evil_cat/actor.py#L494-L500)
- **Description**: Removed the redundant `last_10_history_block` (Section 1) and `last_10_observations_block` (Section 2) from `build_coach_eval_prompt`, enriched `build_decision_prompt`'s 25-tick history lines with `HP: {hp}/{max_hp}`, and appended the current tick's subordinate decision to `latest_actor_payload_block` (Section 3).

### `OPT-01`: Coach 2-Call Thinking Budget Allocation (`Call 1` vs. `Call 2`)
- **Status**: ⏸️ Deferred (Needs Independent Evaluation)
- **Target**: [`actors/cat/subagents/eval.py`](actors/cat/subagents/eval.py), [`actors/evil_cat/eval.py`](actors/evil_cat/eval.py)
- **Description**: Under the 2-call Coach cap, Call 1 selects tools (`get_boxscore`, `get_coverage_map`, `get_event_reel`) while Call 2 receives the tool outputs and synthesizes the final standing directive. Capping Call 2 at `512` thinking tokens starved the Coach of reasoning depth after tool outputs arrived; any future step-down should cap Call 1 (tool selection) while preserving analytical budget on Call 2.

### `OPT-02`: Down-Tick (`action_cooldown > 0`) LLM Call Skipping (`cooldown_fastpath`)
- **Status**: ⛔ Deferred / High Risk (Do Not Enable Without Synchronous `cd=0` Deliberation)
- **Target**: [`actors/cat/api.py`](actors/cat/api.py), [`actors/evil_cat/api.py`](actors/evil_cat/api.py)
- **Description**: Skipping LLM calls during cooldown ticks in `decoupled=True` mode interacts with the 1-tick observation offset between background deliberation and action consumption (`sim-d38a998f` and `sim-5c9ad8d2`). Must not be enabled unless WebSocket `can_act=True` (`cd=0`) observations deliberate directly on the fresh `cd=0` board state within `budget_sec`.

### `OPT-04`: Aggressive Condensation of `actor_skills.md` & `eval_skills.md`
- **Status**: ⛔ Rejected / Dangerous
- **Target**: [`actors/cat/actor_skills.md`](actors/cat/actor_skills.md), [`actors/cat/subagents/eval_skills.md`](actors/cat/subagents/eval_skills.md)
- **Description**: Cutting 70%+ of the markdown skill manuals removed worked examples and coaching anti-patterns, degrading lunge pursuit and causing Coaches to issue self-contradictory directives. Keep full manuals intact.
