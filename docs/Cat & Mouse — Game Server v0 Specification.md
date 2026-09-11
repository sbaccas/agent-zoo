# Cat & Mouse

## Game Server v0 — Technical Specification

## 1. Project Goal

**Cat & Mouse** is a multi-agent simulation environment for experimenting with autonomous AI agents in a shared world.

The initial version consists of:

- one authoritative game server
- a configurable two-dimensional grid
- configurable populations of cats and mice
- a logical simulation clock
- a strict API for agent interaction
- a browser-based simulation control panel
- a live visualization of the game board
- simple non-LLM agents for initial testing

The immediate objective is:

> Build the smallest useful environment in which independent actors can perceive a world, make decisions, act, compete, and be observed experimentally.

The long-term objective is to use the environment to experiment with independently deployed AI agents implemented with technologies such as:

- Python
- Gemini
- Google ADK
- Cloud Run
- Gemini Enterprise Agent Platform / Agent Runtime

The game server itself must remain **independent of Gemini or any particular agent implementation**.

---

# 2. Technology

The project should be implemented entirely in **Python** wherever practical.

Initial stack:

```text
Python 3
FastAPI
Pydantic
Uvicorn
Docker
HTML / CSS / minimal JavaScript
GCP
```

Future agents will also be implemented in Python, likely using Google ADK.

Avoid unnecessary frameworks or infrastructure in v0.

In particular, do not introduce:

```text
React
Redis
Pub/Sub
Cloud SQL
Firestore
Kubernetes
complex event infrastructure
```

unless a concrete requirement later justifies them.

---

# 3. High-Level Architecture

```text
                       HUMAN
                         │
                         ▼
              ┌───────────────────────┐
              │ SIMULATION CONTROL UI │
              │                       │
              │ Start / Pause         │
              │ Add/remove animals    │
              │ Change parameters     │
              │ Live visualization    │
              │ Metrics               │
              └───────────┬───────────┘
                          │
                    Admin API
                          │
                          ▼
              ┌───────────────────────┐
              │      GAME SERVER      │
              │                       │
              │ Authoritative world   │
              │ Simulation clock      │
              │ Scheduler             │
              │ Rules                 │
              │ Entity state          │
              │ Observations          │
              │ Action resolution     │
              └───────────┬───────────┘
                          │
                    Agent API
                 ┌────────┴────────┐
                 │                 │
                 ▼                 ▼
           ┌───────────┐     ┌───────────┐
           │ CAT AGENT │     │MOUSE AGENT│
           │           │     │           │
           │Independent│     │Independent│
           │application│     │application│
           └───────────┘     └───────────┘
```

There are two conceptually separate interfaces:

### Administrative Control Plane

Used by the human experimenter.

Allows:

- creating simulations
- starting and stopping simulations
- pausing/resuming
- changing simulation speed
- adding/removing animals
- changing permitted runtime parameters
- observing global state
- viewing metrics

### Agent/Game Plane

Used by autonomous actors.

Allows:

- obtaining an observation
- submitting a legal action

Agents must never have administrative privileges.

---

# 4. Fundamental Rule

## The server owns reality. Agents own decisions.

An agent may request:

```text
move east
```

It may NOT declare:

```text
my new position is (42, 17)
```

The server:

1. receives an intended action
2. validates the action
3. determines its consequences
4. mutates authoritative state
5. advances simulation time
6. reports the result

Conceptually:

```text
AGENT
  │
  │ intent
  ▼
GAME SERVER
  │
  │ validation
  │ rules
  │ state transition
  ▼
NEW WORLD STATE
```

Agents never directly mutate game state.

---

# 5. World

The world is a discrete two-dimensional grid.

Default:

```text
100 × 100
```

Coordinates:

```text
x ∈ [0, width - 1]
y ∈ [0, height - 1]
```

Configuration:

```json
{
  "width": 100,
  "height": 100
}
```

Grid dimensions must not be hard-coded.

---

# 6. Topology

v0 uses a **bounded square**.

Boundaries are walls.

This intentionally introduces:

- edges
- corners
- trapping opportunities
- spatial constraints useful to predators

For example, from `(0,0)`:

```text
east  = legal
south = legal
north = illegal
west  = illegal
```

However, topology must exist as configuration:

```json
{
  "topology": "bounded"
}
```

Future:

```json
{
  "topology": "torus"
}
```

For toroidal topology:

```text
(99, 50) + EAST → (0, 50)
(0, 50)  + WEST → (99, 50)
```

The architecture should allow topology to change without changing the agent API.

---

# 7. Entity Model

Every animal is an entity.

Minimum representation:

```json
{
  "id": "mouse-143",
  "type": "mouse",
  "position": {
    "x": 71,
    "y": 12
  },
  "alive": true
}
```

Initial entity types:

```text
cat
mouse
```

The architecture must NOT assume these are the only possible types.

Future entities may include:

```text
squirrel
wolf
food
shelter
obstacle
resource
```

Species-specific behavior should therefore be configuration-driven where reasonable.

---

# 8. Species Configuration

Species should have configurable attributes.

Example conceptual model:

```json
{
  "mouse": {
    "vision_radius": 4,
    "action_budget": 1,
    "weight": 1
  },
  "cat": {
    "vision_radius": 8,
    "action_budget": 2,
    "weight": 10
  }
}
```

v0 does not need a sophisticated biological model.

However, do not bury species characteristics throughout game logic.

Keep them centralized/configurable.

---

# 9. Logical Simulation Clock

The game maintains an internal **logical clock**.

Simulation time is measured in:

```text
ticks
rounds
```

Ticks are monotonically increasing integers:

```text
0
1
2
3
...
```

A tick represents a unit of simulation progress.

A round represents one complete scheduling cycle.

Example:

```text
Round 0

Tick 0 → Cat
Tick 1 → Mouse

Round 1

Tick 2 → Cat
Tick 3 → Mouse
```

With additional animals:

```text
Round 20

Tick 60 → Cat
Tick 61 → Mouse
Tick 62 → Squirrel
```

The precise scheduler may evolve later.

The important invariant is:

> Simulation time is independent of wall-clock execution time.

A Gemini call taking eight seconds must not cause the animal to biologically age eight times more than an agent whose inference took one second.

---

# 10. Wall-Clock Time

Real timestamps may be recorded for telemetry:

```json
{
  "tick": 938,
  "timestamp": "2026-09-11T12:42:18Z",
  "actor": "mouse-14",
  "action": "west"
}
```

However:

```text
timestamp = operational telemetry
tick      = game physics
```

Wall-clock time must not determine game outcomes in v0.

---

# 11. Action Model

The action architecture should allow different species to eventually have different capabilities and speeds.

Conceptually, actions may have:

```text
cost
duration
range
parameters
```

Example future definition:

```json
{
  "name": "pounce",
  "cost": 2,
  "duration_ticks": 3,
  "range": 2
}
```

This allows future behaviors such as:

- cats moving twice
- mice moving once
- mice dashing
- cats taking multiple swipes
- attacks requiring multiple ticks
- prey escaping while an attack is being prepared

However:

## Do not implement this complexity unnecessarily in v0.

v0 requires only:

```text
north
south
east
west
stay
```

Movement costs one action unit and resolves immediately.

The data model should simply avoid preventing richer actions later.

---

# 12. Action Budget

Species may have configurable action budgets.

Initial example:

```json
{
  "cat": {
    "action_budget": 2
  },
  "mouse": {
    "action_budget": 1
  }
}
```

This allows a cat, for example, to perform:

```text
move
move
```

while a mouse receives one movement opportunity.

Exact balancing is experimental and should therefore be configurable rather than embedded as a permanent rule.

---

# 13. Conflict Resolution, Collision & Displacement

When multiple entities attempt to occupy the same square or enter a contested tile, resolution is governed by species **weight**:

```text
cat.weight   = 10
mouse.weight = 1
```

### Resolution Rules:

1. **Heavier Entity Wins**: When a heavier animal (Cat) moves onto or contests a tile with a lighter animal (Mouse), the Cat claims the tile.
2. **Displacement / Bounce**:
   - Rather than instantaneous capture on contact, the lighter animal (Mouse) is bounced / displaced 1–2 squares away into an adjacent valid, unblocked tile.
   - This prevents instantaneous collapse and immediately initiates an evasive chase/pursuit dynamic (the mouse attempts to dash away while the cat initiates pursuit or attack).
3. **Same-Weight Conflict**:
   - If two entities of equal weight (e.g. Mouse vs. Mouse, or Cat vs. Cat) contest the same tile, the movement is blocked or both entities remain in / bounce back to their prior positions.

Future versions will build upon this foundation to introduce:

```text
attack
radius of effect (AoE)
dash / evasion burst
swipe
pounce
health
damage
```

---

# 14. Observation Model

Agents do NOT receive unrestricted world state.

They request an **observation**.

Conceptual endpoint:

```http
GET /simulations/{simulation_id}/agents/{agent_id}/observation
```

Example:

```json
{
  "simulation_id": "sim-123",
  "tick": 482,
  "round": 241,
  "self": {
    "id": "mouse-1",
    "type": "mouse",
    "position": {
      "x": 37,
      "y": 61
    }
  },
  "visible_entities": [
    {
      "id": "cat-3",
      "type": "cat",
      "position": {
        "x": 40,
        "y": 60
      }
    }
  ],
  "legal_actions": [
    "north",
    "south",
    "east",
    "west",
    "stay"
  ]
}
```

---

# 15. Partial Observability

Observation must be entity-specific.

Agents should not simply call:

```text
GET /world
```

and receive omniscient state.

Each species has a configurable observation radius.

Example:

```text
Mouse vision radius = 4
Cat vision radius   = 8
```

The server determines what the animal can see.

The initial implementation may use a simple distance rule.

Future observation models may include:

```text
directional vision
field of view
hearing
occlusion
smell
memory
```

The agent-facing API must not need to change when these are introduced.

---

# 16. Agent Architecture & Turn Orchestration

Turn orchestration uses an **engine-driven RPC model (Option C)**:

- Agents run in independent containers hosting lightweight HTTP servers.
- When an animal is scheduled to act in a tick, the **game engine** drives the turn by sending an HTTP request containing the current entity observation directly to the agent's container.
- The agent container evaluates the observation and returns its chosen action synchronously.
- The server validates the action, resolves movement/collisions, updates world state, and advances the clock.

This architecture eliminates aggressive polling loops and synchronization races while keeping agent decisions decoupled from game mechanics. (Future versions can upgrade this transport to WebSockets without changing the core payload contract).

Agent flow:

```text
Game Engine (Scheduler)
        │
        ▼ (POST /act with observation)
Agent Container Service (Cat / Mouse)
        │
        │ decide(observation)
        ▼
Action Response ({"action": "east"})
        │
        ▼
Game Engine validates & mutates state
```

Initial test implementation:

```python
# random_cat or random_mouse agent server endpoint:
@app.post("/act")
def act(observation: Observation) -> ActionResponse:
    action = random.choice(observation.legal_actions)
    return ActionResponse(action=action)
```

Future implementation:

```text
observation
    ↓
Gemini / ADK (hosted in agent container)
    ↓
decision
    ↓
ActionResponse
```

---

# 17. Minimum Agent Endpoints & Interface Contract

## 1. Engine-to-Agent Invocation (Turn Dispatch)

When it is an entity's turn, the game engine calls the agent's configured endpoint:

```http
POST {agent_service_url}/act
Content-Type: application/json
```

Request (Engine → Agent):

```json
{
  "simulation_id": "sim-123",
  "tick": 482,
  "round": 241,
  "self": {
    "id": "mouse-1",
    "type": "mouse",
    "position": {
      "x": 37,
      "y": 61
    }
  },
  "visible_entities": [
    {
      "id": "cat-3",
      "type": "cat",
      "position": {
        "x": 40,
        "y": 60
      }
    }
  ],
  "legal_actions": [
    "north",
    "south",
    "east",
    "west",
    "stay"
  ]
}
```

Response (Agent → Engine):

```json
{
  "action": "east"
}
```

## 2. Server Direct Fallback Endpoints (Manual / Testing)

For manual testing, interactive stepping, or non-service actors:

### Get Observation
```http
GET /simulations/{simulation_id}/agents/{agent_id}/observation
```

### Submit Action
```http
POST /simulations/{simulation_id}/agents/{agent_id}/actions
```

---

# 18. Simulation Controller

The server must support running a simulation autonomously.

A simulation has states such as:

```text
created
running
paused
stopped
completed
```

Administrative operations should include:

```http
POST /simulations
POST /simulations/{id}/start
POST /simulations/{id}/pause
POST /simulations/{id}/resume
POST /simulations/{id}/step
POST /simulations/{id}/stop
```

`step` advances the simulation manually by one logical scheduling step while paused.

---

# 19. Creating a Simulation

The UI/API should allow configuration such as:

```json
{
  "width": 100,
  "height": 100,
  "topology": "bounded",

  "populations": {
    "cats": 10,
    "mice": 100
  },

  "species": {
    "cat": {
      "vision_radius": 8,
      "action_budget": 2
    },
    "mouse": {
      "vision_radius": 4,
      "action_budget": 1
    }
  }
}
```

The server randomly assigns valid initial positions unless explicitly configured otherwise.

---

# 20. Runtime Population Changes

The human experimenter must be able to modify populations while a simulation is running.

Examples:

```text
+1 cat
+10 cats
+100 mice

-1 cat
-10 cats
-100 mice
```

Conceptual endpoint:

```http
POST /simulations/{id}/entities
```

Example:

```json
{
  "type": "mouse",
  "count": 100
}
```

The server creates entities and selects valid spawn locations.

Removal should also be supported through an administrative operation.

These operations are available only through the administrative control plane.

An autonomous mouse cannot call the administrative endpoint to create 500 mice.

Future reproduction must exist as an explicit game mechanic.

---

# 21. Runtime Parameter Changes

Where practical, parameters should be adjustable during an experiment.

Potential hot-adjustable parameters:

```text
population
simulation speed
vision radius
action budget
```

Future:

```text
reproduction rate
energy consumption
attack duration
food spawn rate
```

Structural parameters may require a new simulation:

```text
board dimensions
topology
scheduler type
```

The API should clearly distinguish runtime-mutable and creation-time configuration.

---

# 22. Simulation Speed

The human interface should allow control over how quickly the server executes logical ticks.

Example:

```text
1 tick/sec
5 ticks/sec
10 ticks/sec
maximum
```

This controls **execution speed**, not simulation physics.

For example:

```text
Tick 500 → Tick 501
```

has the same simulation meaning whether the server waits:

```text
1 second
100 milliseconds
0 milliseconds
```

between them.

---

# 23. Live Simulation Control Panel

The application must provide a simple browser interface.

This is not merely a passive visualization.

It is the **experimental control surface**.

Conceptually:

```text
┌─────────────────────────────────────────────┐
│ CAT & MOUSE — SIMULATION 17                 │
│                                             │
│ Status: RUNNING                             │
│ Tick: 14,381        Round: 7,190            │
│                                             │
│ Cats: 47            Mice: 611               │
│                                             │
│ [Pause] [Step] [Resume] [Stop] [Reset]      │
│                                             │
│ Tick rate: [ 10 / second ]                  │
│                                             │
│ Add/remove:                                 │
│ [+1 Cat] [+10 Cats]                         │
│ [-1 Cat] [-10 Cats]                         │
│                                             │
│ [+1 Mouse] [+10 Mice] [+100 Mice]           │
│ [-1 Mouse] [-10 Mice] [-100 Mice]           │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│                 LIVE BOARD                  │
│                                             │
│ . . M . . . . C . . . M . . . . . .      │
│ . . . C . M . . . . . . . M . . . .      │
│ M . . . . . M . . C . . . . . . . .      │
│ . . . . M . . . . . . . C . . . . .      │
│                                             │
├─────────────────────────────────────────────┤
│ Captures: 391                               │
│ Current Cats: 47                            │
│ Current Mice: 611                           │
│                                             │
└─────────────────────────────────────────────┘
```

The exact visual design is not important in v0.

Functionality is.

---

# 24. Board Visualization

The browser must display the current two-dimensional world.

Initial symbols:

```text
C = Cat
M = Mouse
. = Empty
```

For example:

```text
+--------------------+
|....................|
|..............C.....|
|....................|
|....................|
|..........M.........|
|....................|
+--------------------+
```

As simulation ticks resolve, entities visibly move across the board.

### Rendering Technology: HTML5 `<canvas>`

For performant, responsive rendering of large grids (such as 100×100 = 10,000 cells):

- An **HTML5 `<canvas>`** element must be used rather than thousands of DOM `<div>` nodes or preformatted text blocks.
- The canvas will automatically adapt and scale responsively to fit the browser viewport or panel container.
- High-performance 2D context redraws (sub-millisecond) will render the grid background, cats (orange/red `C`), and mice (blue/gray `M`).
- The display remains strictly grid-based and crisp at any scale.

---

# 25. Visualization Architecture

The visualization is an **observer**.

It may read global state because it belongs to the administrative interface.

It must not bypass server rules when changing game state.

All administrative mutations must still go through explicit server operations.

Conceptually:

```text
                       GAME SERVER
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            ▼              ▼              ▼
        CAT AGENT      MOUSE AGENT    CONTROL UI
                                        │
                                        ▼
                                   LIVE BOARD
```

The UI must not contain authoritative game logic.

---

# 26. Live Updates

v0 may use simple polling.

For example:

```http
GET /simulations/{id}/state
```

every few hundred milliseconds.

This is sufficient initially.

A future version may replace polling with:

```text
WebSocket
Server-Sent Events
```

Do not introduce those unless necessary.

---

# 27. Global State Endpoint

The administrative UI requires global state.

Example:

```http
GET /simulations/{id}/state
```

Possible response:

```json
{
  "simulation_id": "sim-17",
  "status": "running",
  "tick": 14381,
  "round": 7190,
  "width": 100,
  "height": 100,
  "entities": [
    {
      "id": "cat-1",
      "type": "cat",
      "x": 31,
      "y": 17
    },
    {
      "id": "mouse-1",
      "type": "mouse",
      "x": 48,
      "y": 32
    }
  ]
}
```

This endpoint is for administration/visualization.

Agents must not use it.

---

# 28. Metrics

v0 should collect basic counters.

At minimum:

```text
current tick
current round
current cat population
current mouse population
captures
```

The architecture should make future metrics straightforward:

```text
average lifespan
distance traveled
capture rate
birth rate
death rate
messages sent
messages received
population over time
resource consumption
```

Do NOT build a separate analytics system for v0.

Simple in-memory counters are sufficient.

---

# 29. Event Model

Important state transitions should be representable as events.

Example:

```json
{
  "tick": 438,
  "type": "move",
  "actor": "cat-7",
  "from": [31, 17],
  "to": [32, 17]
}
```

Capture:

```json
{
  "tick": 712,
  "type": "capture",
  "actor": "cat-7",
  "target": "mouse-41",
  "position": [52, 19]
}
```

This does not require a message bus.

An in-memory event history is sufficient initially.

---

# 30. Initial Non-AI Actors

Before Gemini integration, implement simple Python actors.

For example:

```text
random_cat.py
random_mouse.py
```

Each actor:

1. obtains an observation
2. reads legal actions
3. selects a random legal action
4. submits it
5. repeats when scheduled

This validates the environment independently of AI.

---

# 31. Initial Deployment

The server should be containerized.

Suggested path:

```text
Python
  ↓
FastAPI
  ↓
Docker
  ↓
Artifact Registry
  ↓
Cloud Run
```

A VM/GCE deployment is acceptable if useful during experimentation.

Because v0 uses in-memory authoritative state, horizontally scaling multiple independent game-server instances would create consistency problems.

Therefore v0 should run as a single authoritative server instance.

Do not solve distributed state management yet.

---

# 32. Future Agent Deployment

Once the environment works, replace simple actors with independent Python agents.

Conceptually:

```text
                   GAME SERVER
                       │
            ┌──────────┴──────────┐
            │                     │
            ▼                     ▼

       CAT AGENT              MOUSE AGENT

       Python                 Python
       ADK                    ADK
       Gemini                 Gemini

       Cloud Run              Cloud Run
          or                     or
       Agent Runtime          Agent Runtime
```

The game server API should require no changes.

---

# 33. Future Species

The architecture should allow additional independently acting species.

Example:

```text
                    GAME SERVER
                         │
           ┌─────────────┼─────────────┐
           │             │             │
           ▼             ▼             ▼
          CAT          MOUSE        SQUIRREL
```

Different species may have different:

```text
vision
speed
actions
goals
communication abilities
memory
```

---

# 34. Future Agent Communication

Mouse and Squirrel may eventually cooperate by exchanging information about predators.

Agents must not gain magical shared state.

Communication should happen through explicit game interfaces.

Potential future operations:

```text
send_message()
read_messages()
broadcast()
```

Example:

```text
Mouse observes Cat at (42,18).

Mouse → Squirrel:

"Predator observed near (42,18)."
```

The receiving agent may then incorporate that information into its own decision.

This creates genuine multi-agent information sharing.

---

# 35. Future Biological Mechanics

Potential future mechanics include:

```text
health
energy
age
food
reproduction
starvation
injury
```

For example:

```text
Mouse reproduces
       │
       ▼
Server validates reproduction conditions
       │
       ▼
new Mouse entity created
```

This is fundamentally different from an agent using the administrative population-control API.

---

# 36. Future Advanced Actions

The architecture should eventually permit actions that require multiple logical ticks.

Example:

```text
CAT begins pounce
      │
      ▼
duration = 3 ticks
      │
      ├── Mouse acts
      ├── Mouse moves
      │
      ▼
pounce resolves
```

This permits:

- slow attacks
- evasive behavior
- interruptible actions
- different species speeds
- strategic timing

Do not implement this until basic movement works.

---

# 37. Future Experimental Questions

The environment should eventually allow experiments such as:

```text
How does predator success change with vision radius?

How does removing corners affect capture rate?

What happens when mouse speed increases?

What predator/prey population ratio is stable?

Does communication improve prey survival?

Can different prey species cooperate?

Do agents learn useful communication conventions?

Does deception emerge?

How does agent memory affect survival?
```

Different populations may eventually receive different system instructions.

Example:

```text
Mouse A:
Prioritize immediate survival.

Mouse B:
Warn nearby prey whenever you observe a predator.

Mouse C:
Help agents that previously helped you.

Mouse D:
You may deceive another agent when doing so increases
your expected survival.
```

The game environment should remain unchanged while agent behavior changes.

---

# 38. Architectural Separation

Keep these concerns strictly separate:

```text
ENVIRONMENT
Game Server

DECISION MAKING
Agent

MODEL
Gemini

EXPERIMENT CONTROL
Human / Control UI
```

Therefore:

```text
Game Server
    does not depend on Gemini.

Agent
    does not directly mutate world state.

Gemini
    does not need infrastructure knowledge.

Control UI
    does not implement game physics.
```

---

# 39. Suggested Python Structure

Keep the initial repository simple.

```text
cat-and-mouse/
│
├── app/
│   ├── main.py
│   │
│   ├── api/
│   │   ├── simulations.py
│   │   ├── agents.py
│   │   └── admin.py
│   │
│   ├── game/
│   │   ├── world.py
│   │   ├── simulation.py
│   │   ├── scheduler.py
│   │   ├── actions.py
│   │   ├── observations.py
│   │   └── entities.py
│   │
│   ├── models/
│   │   ├── entity.py
│   │   ├── simulation.py
│   │   └── action.py
│   │
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── styles.css
│
├── actors/
│   ├── random_cat.py
│   └── random_mouse.py
│
├── tests/
│   ├── test_world.py
│   ├── test_actions.py
│   ├── test_observations.py
│   └── test_simulation.py
│
├── Dockerfile
├── requirements.txt
└── README.md
```

Do not over-engineer this structure if a simpler implementation naturally emerges.

---

# 40. v0 Definition of Done

v0 is complete when all of the following work:

1. Python/FastAPI game server runs locally.
2. A simulation can be created.
3. Grid dimensions are configurable.
4. Bounded topology works.
5. Configurable Cat and Mouse populations can be spawned.
6. Every entity has unique identity and position.
7. The server maintains a logical tick counter.
8. The simulation can start automatically.
9. The simulation can be paused.
10. A paused simulation can advance manually.
11. The simulation can resume.
12. Simulation execution speed can be changed.
13. Animals can request entity-specific observations.
14. Observation radius is configurable by species.
15. Legal movement is validated by the server.
16. Entities can move north, south, east, west, or stay.
17. Cat and Mouse action budgets can differ.
18. Weight-based conflict resolution: heavier animals (Cat) claim contested squares, displacing lighter animals (Mouse) 1–2 tiles.
19. Population counts can be changed administratively while running.
20. The browser displays the current game board rendered using a responsive HTML5 `<canvas>`.
21. Cats appear as `C`.
22. Mice appear as `M`.
23. The board visibly evolves while the simulation runs.
24. The UI displays tick, round, population, and capture information.
25. Two simple Python actor server implementations (random_cat and random_mouse) receive engine-driven RPC calls and return decisions without Gemini.
26. Basic events and metrics are recorded.
27. The application has automated tests for core game rules.
28. The server can be containerized.
29. The container can be deployed to GCP.

---

# 41. Explicitly Out of Scope for v0

Do **not** build the following yet:

```text
Gemini integration
ADK integration
Agent Runtime
MCP
agent-to-agent messaging
squirrels
wolves
food
reproduction
energy
health
complex attacks
multi-tick attacks
memory
databases
distributed state
Pub/Sub
Redis
WebSockets
sophisticated frontend frameworks
authentication beyond what is necessary for deployment
```

These are future experiments.

First make the world work.

---

# 42. Guiding Principle

**Keep the world simple and make the inhabitants increasingly sophisticated.**

The game server provides:

```text
space
time
rules
observation
action
state
```

The agents provide:

```text
reasoning
strategy
memory
communication
cooperation
competition
```

The human experimenter controls:

```text
initial conditions
population
parameters
simulation speed
experiments
```

The initial milestone is therefore:

> Build a deterministic, observable, controllable two-dimensional world in Python where populations of independent actors can move, perceive, hunt, evade, and eventually serve as hosts for autonomous AI agents.