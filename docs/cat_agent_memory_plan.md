# Cat Agent Memory Architecture & Implementation Plan

**Date:** 2026-09-13  
**Project:** Cat and Mouse autonomous agent simulation  
**Audience:** Implementation handoff for Jetski  
**Status:** Ready for implementation

---

# 1. Objective

Implement a three-lane memory architecture for autonomous cat agents while keeping the LLM as the primary decision-maker.

The goal is **not** to hardcode tactical behavior into the engine. The engine should provide state, memory infrastructure, and retrieval primitives. Gemini should remain responsible for deciding what action to take and, where appropriate, what experiences are important enough to remember.

The three memory lanes are:

1. **Working memory**
   - Short-lived.
   - High fidelity.
   - Always included in the prompt.
   - Current implementation already approximates this with the previous 3 ticks.

2. **Episodic memory**
   - Specific remembered experiences.
   - Example: `"Mouse-2 escaped into burrow-1 after I chased it south."`
   - Stored persistently.
   - Retrieved selectively.

3. **Semantic memory**
   - Generalized beliefs or learned facts synthesized from multiple experiences.
   - Example: `"Burrow-1 is a common mouse escape route."`
   - Stored persistently.
   - Retrieved selectively.

Core rule:

> **Storage is not context.**
>
> A cat can have thousands of stored memories, but only a small number should be injected into any one Gemini call.

---

# 2. Design Principles

## 2.1 LLM remains the policy engine

Do not convert tactical rules such as attack, lunge, pursuit, or escape into deterministic game-engine behavior unless needed for legality or game mechanics.

The engine may compute factual state, legal actions, distances, event metadata, etc., but Gemini should choose actions.

The system should remain as unopinionated as practical about strategy.

---

## 2.2 Context growth must be bounded

The previous ADK implementation accumulated complete session history and caused prompt/token growth and extreme latency.

The new architecture must maintain a roughly stable prompt size per decision.

Target shape:

```text
skills.md
+ current observation
+ working memory (last 3 ticks)
+ top episodic memories
+ top semantic memories
→ Gemini
```

Prompt size should grow only with configured `top_k` values, never with total simulation lifetime.

---

## 2.3 Separate memory formation, memory storage, memory retrieval, and memory consolidation

These are distinct operations:

```text
WORLD EVENT
    ↓
MEMORY CANDIDATE
    ↓
MEMORY SELECTION
    ↓
EPISODIC STORAGE
    ↓
RETRIEVAL
    ↓
PROMPT CONTEXT

episodic memories
    ↓
REFLECTION / CONSOLIDATION
    ↓
SEMANTIC MEMORY
```

Do not conflate these layers.

---

## 2.4 Start with simple primitives

Initial implementation should use:

- Python/application state for working memory.
- PostgreSQL for episodic and semantic memory.
- Metadata/rule-based retrieval first.
- No vector database required in V1.
- No dedicated memory sub-agent in V1.
- No Agent Runtime / Memory Bank yet.
- No RAG framework yet.

Later versions can add:

- pgvector.
- embeddings.
- hybrid retrieval.
- dedicated memory agents.
- Agent Runtime.
- Google Memory Bank.

The point of V1 is to understand the mechanics directly.

---

# 3. High-Level Architecture

```text
                              ┌────────────────────┐
                              │    GAME ENGINE     │
                              │                    │
                              │ world state        │
                              │ ticks              │
                              │ legal actions      │
                              │ event generation   │
                              └─────────┬──────────┘
                                        │
                                        │ Observation
                                        ▼
                              ┌────────────────────┐
                              │     CAT AGENT      │
                              │                    │
                              │ ADK + Gemini       │
                              └─────────┬──────────┘
                                        │
                       ┌────────────────┼────────────────┐
                       │                │                │
                       ▼                ▼                ▼
                WORKING MEMORY    EPISODIC MEMORY   SEMANTIC MEMORY
                last 3 ticks      PostgreSQL         PostgreSQL
                       │                │                │
                       └──────────┬─────┴─────┬──────────┘
                                  │           │
                                  ▼           ▼
                             CONTEXT BUILDER
                                  │
                                  ▼
                                GEMINI
                                  │
                                  ▼
                                ACTION
                                  │
                                  ▼
                              GAME ENGINE
```

---

# 4. Working Memory

## 4.1 Current behavior

The current ADK query code already constructs a sliding three-tick context:

```python
for h in (history[-3:] if history else []):
    ...
```

This should remain the basis of working memory.

---

## 4.2 Recommended representation

If working memory is managed explicitly in the agent service, use:

```python
from collections import deque

working_memory = deque(maxlen=3)
```

Suggested structure:

```python
@dataclass
class WorkingMemoryEntry:
    tick: int
    position: dict[str, int]
    facing: str
    visible_entities: list[dict]
    action: str
    thought: str
    strategy: str | None
    target_mouse_id: str | None
```

The exact source can continue to be the game server's history payload if that is simpler.

---

## 4.3 Prompt policy

Working memory is:

- always included,
- max 3 entries,
- high fidelity,
- never persisted as long-term memory by default merely because it exists.

Working memory is not the same thing as episodic memory.

---

# 5. Episodic Memory

## 5.1 Definition

Episodic memory stores specific experiences from the cat's point of view.

Examples:

```text
"I saw mouse-2 near the west hallway."
"Betsy attacked me while I was pursuing mouse-3."
"Mouse-4 escaped into burrow-1."
"I found cheese near coordinate (8, 14)."
"I chased mouse-1 for four ticks and lost sight of it."
```

These are concrete events, not generalized beliefs.

---

## 5.2 PostgreSQL schema

Initial recommended table:

```sql
CREATE TABLE cat_episodes (
    id UUID PRIMARY KEY,
    cat_id TEXT NOT NULL,
    game_id TEXT NOT NULL,

    tick INTEGER NOT NULL,
    round INTEGER,

    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,

    location_x INTEGER,
    location_y INTEGER,

    entity_ids TEXT[] NOT NULL DEFAULT '{}',

    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,

    source_event JSONB,
    metadata JSONB NOT NULL DEFAULT '{}',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Indexes:

```sql
CREATE INDEX idx_cat_episodes_cat_tick
    ON cat_episodes (game_id, cat_id, tick DESC);

CREATE INDEX idx_cat_episodes_event_type
    ON cat_episodes (game_id, cat_id, event_type);

CREATE INDEX idx_cat_episodes_entities
    ON cat_episodes USING GIN (entity_ids);

CREATE INDEX idx_cat_episodes_created
    ON cat_episodes (created_at DESC);
```

---

## 5.3 Memory candidate events

The game engine should produce **candidate events**, not final memories.

Candidate event types may include:

```text
entity_seen
entity_lost
mouse_seen
mouse_lost
mouse_attacked
mouse_killed
mouse_escaped
cat_seen
cat_attacked_me
i_attacked_cat
food_seen
food_eaten
burrow_seen
entered_new_area
collision
failed_action
conversation
resource_contested
long_chase_started
long_chase_ended
```

Do not automatically store every tick.

The event generator should detect meaningful changes in state.

Example candidate:

```json
{
  "event_type": "mouse_escaped",
  "tick": 143,
  "cat_id": "cat-1",
  "game_id": "game-42",
  "location": {"x": 12, "y": 8},
  "entities": ["mouse-2", "burrow-1"],
  "details": {
    "chase_duration_ticks": 5,
    "escape_destination": "burrow-1"
  }
}
```

---

# 6. Memory Selection

## 6.1 Goal

The system should decide whether a candidate event is worth preserving as episodic memory.

The eventual goal is to allow Gemini to participate in this decision so memory formation itself can differ between agents.

However, V1 should avoid adding a second LLM call on every tick.

---

## 6.2 Recommended V1 strategy: hybrid gating

### Stage A — deterministic candidate filtering

Only invoke memory selection for event classes that could plausibly matter.

Examples:

```python
MEMORY_CANDIDATE_EVENTS = {
    "mouse_seen",
    "mouse_escaped",
    "mouse_killed",
    "cat_attacked_me",
    "i_attacked_cat",
    "food_seen",
    "food_eaten",
    "burrow_seen",
    "entered_new_area",
    "conversation",
}
```

Ignore routine events such as:

```text
moved north one tile
stayed
facing changed
ordinary empty observation
```

unless a later experiment intentionally wants those.

---

### Stage B — LLM memory selection

For candidate events, Gemini may be asked:

```text
Given this event and the cat's current perspective:

1. Is this experience worth remembering?
2. If yes:
   - provide a concise first-person memory summary,
   - assign importance from 0.0 to 1.0,
   - list relevant entity IDs,
   - identify a memory category.
3. If no, return remember=false.
```

Recommended structured output:

```python
class EpisodicMemoryDecision(BaseModel):
    remember: bool
    summary: str = ""
    importance: float = 0.0
    category: str = ""
    entity_ids: list[str] = []
```

Important:

- Do not call this on every movement tick.
- Only call it when candidate events exist.
- Multiple candidate events in one tick should be batched into one memory-selection prompt where possible.

---

## 6.3 Alternative V1.0

If latency becomes a problem, initially write all candidate events using deterministic summaries and importance defaults.

Example:

```python
if event.type == "cat_attacked_me":
    importance = 0.9
```

Then replace deterministic selection with Gemini after the rest of the pipeline works.

Do not block the entire memory architecture on LLM-based selection.

---

# 7. Semantic Memory

## 7.1 Definition

Semantic memory stores beliefs, facts, relationships, regularities, or inferred world knowledge that persist beyond one episode.

Examples:

```text
"Betsy is usually hostile toward me."
"Burrow-1 is a common mouse escape route."
"Cheese is often found near the southeast room."
"Mouse-2 tends to flee south when pursued."
```

Semantic memories should usually be derived from multiple episodes or explicit reflection.

---

## 7.2 PostgreSQL schema

Recommended V1 schema:

```sql
CREATE TABLE cat_semantic_memories (
    id UUID PRIMARY KEY,
    cat_id TEXT NOT NULL,
    game_id TEXT NOT NULL,

    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,

    summary TEXT NOT NULL,

    confidence REAL NOT NULL DEFAULT 0.5,
    evidence_count INTEGER NOT NULL DEFAULT 1,

    source_episode_ids UUID[] NOT NULL DEFAULT '{}',

    metadata JSONB NOT NULL DEFAULT '{}',

    first_learned_tick INTEGER,
    last_updated_tick INTEGER,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Indexes:

```sql
CREATE INDEX idx_cat_semantic_cat
    ON cat_semantic_memories (game_id, cat_id);

CREATE INDEX idx_cat_semantic_subject
    ON cat_semantic_memories (game_id, cat_id, subject);

CREATE INDEX idx_cat_semantic_predicate
    ON cat_semantic_memories (game_id, cat_id, predicate);
```

---

## 7.3 Example rows

```json
{
  "subject": "betsy",
  "predicate": "relationship",
  "object": "hostile",
  "summary": "Betsy has behaved aggressively toward me several times.",
  "confidence": 0.76,
  "evidence_count": 3
}
```

```json
{
  "subject": "burrow-1",
  "predicate": "mouse_escape_route",
  "object": "frequent",
  "summary": "Mice frequently escape through burrow-1.",
  "confidence": 0.82,
  "evidence_count": 4
}
```

---

# 8. Semantic Consolidation / Reflection

## 8.1 Purpose

Semantic memory should not be created from every episode immediately.

Instead, multiple episodes should occasionally be consolidated into higher-order beliefs.

Example:

```text
Episode 1:
"Betsy attacked me near the barn."

Episode 2:
"Betsy attacked me near cheese."

Episode 3:
"Betsy chased me away from mouse-2."

        ↓ reflection

Semantic memory:
"Betsy is usually hostile toward me."
```

---

## 8.2 Reflection triggers

Recommended triggers:

### Option A — explicit THINK action

When the cat chooses a future `think` action:

```text
THINK
→ retrieve wider episodic context
→ reflect
→ create/update semantic memories
→ consume multiple simulation ticks
```

This is the preferred long-term design because cognition becomes part of the game economy.

---

### Option B — periodic consolidation

Temporary implementation before THINK exists:

```python
if tick % REFLECTION_INTERVAL == 0:
    consolidate_memories(cat_id)
```

Suggested starting interval:

```text
every 25–50 ticks
```

Do not reflect every tick.

---

### Option C — event threshold

Trigger reflection when enough related episodes accumulate:

```text
>= 3 episodes involving Betsy
>= 3 mouse escapes through same burrow
>= 3 food encounters in same area
```

---

## 8.3 Reflection prompt

Example:

```text
You are reviewing the cat's remembered experiences.

Episodes:
1. ...
2. ...
3. ...

Identify any durable generalizations worth retaining.

Rules:
- Do not invent facts unsupported by the episodes.
- Prefer beliefs that will help future decisions.
- Update confidence based on strength and consistency of evidence.
- Do not merely restate one episode.
- Return zero or more semantic memories.
```

Structured output:

```python
class SemanticMemoryCandidate(BaseModel):
    subject: str
    predicate: str
    object: str
    summary: str
    confidence: float
    source_episode_ids: list[str]
```

---

# 9. Semantic Memory Merge / Update Logic

Before inserting a semantic memory:

1. Search for an existing record with same or similar:
   - cat_id
   - game_id
   - subject
   - predicate

2. If none exists:
   - insert a new semantic memory.

3. If one exists:
   - update object/summary if needed,
   - increment evidence count,
   - update confidence,
   - append source episode IDs,
   - update `last_updated_tick`.

Initial exact-match logic is acceptable.

Later versions can use embeddings to merge semantically similar beliefs.

---

# 10. Retrieval

Retrieval should happen before every Gemini decision, but it must be cheap and bounded.

---

## 10.1 Episodic retrieval V1

Start with metadata/rule-based scoring.

Candidate query should prioritize:

- matching entity IDs,
- current/nearby location,
- recency,
- importance,
- event relevance.

Suggested scoring function:

```python
score = (
    entity_overlap_score
    + location_score
    + recency_score
    + importance_score
    + event_type_score
)
```

Example weights:

```python
score = (
    3.0 * entity_overlap
    + 2.0 * location_relevance
    + 1.5 * recency
    + 2.0 * importance
    + 1.0 * event_type_relevance
)
```

These are starting values only.

---

## 10.2 Entity overlap

If current visible entities include:

```text
["betsy", "mouse-2"]
```

then episodes mentioning either should receive a large boost.

Example:

```python
entity_overlap = (
    len(set(current_entity_ids) & set(memory.entity_ids))
    / max(len(set(current_entity_ids)), 1)
)
```

---

## 10.3 Spatial relevance

Use Manhattan distance initially:

```python
distance = abs(memory_x - current_x) + abs(memory_y - current_y)
```

Possible normalized score:

```python
location_relevance = 1 / (1 + distance)
```

If location is null, score 0.

---

## 10.4 Recency

Example:

```python
age = current_tick - memory.tick
recency = 1 / (1 + age / 10)
```

This causes memories to decay gradually rather than disappear.

---

## 10.5 Importance

Use the stored `importance` value directly:

```python
importance_score = memory.importance
```

---

## 10.6 Retrieval count

Start with:

```text
episodic_top_k = 2 or 3
semantic_top_k = 2 or 3
```

Do not return more simply because more memories exist.

---

# 11. Semantic Retrieval V1

Semantic retrieval can begin with:

- subject match against visible entities,
- predicate relevance,
- recently updated memories,
- confidence.

Example score:

```python
score = (
    3.0 * subject_match
    + 1.5 * confidence
    + 1.0 * recency
)
```

Example:

Current visible entity:

```text
betsy
```

Relevant semantic memory:

```text
Betsy is usually hostile toward me.
```

Should rank highly.

---

# 12. Context Builder

Create a dedicated context builder rather than assembling memory text directly inside `query_adk_gemini`.

Suggested interface:

```python
class CatContextBuilder:
    async def build_context(
        self,
        obs: Observation,
        working_history: list[dict],
        cat_id: str,
        game_id: str,
    ) -> CatDecisionContext:
        ...
```

Suggested object:

```python
@dataclass
class CatDecisionContext:
    working_memory: list[WorkingMemoryEntry]
    episodic_memories: list[EpisodicMemory]
    semantic_memories: list[SemanticMemory]
```

Then render:

```python
def render_memory_context(ctx: CatDecisionContext) -> str:
    ...
```

---

# 13. Prompt Format

Target prompt:

```text
--- RECENT TACTICAL CONTEXT (Last 3 Ticks) ---
...

--- RELEVANT EPISODIC MEMORIES ---
1. Tick 143: Mouse-2 escaped through burrow-1 after a chase.
2. Tick 117: I last saw Mouse-2 near the western corridor.

--- RELEVANT LEARNED BELIEFS ---
1. Burrow-1 is a common mouse escape route. (confidence: 0.82)
2. Mouse-2 often flees south when pursued. (confidence: 0.67)

--- CURRENT OBSERVATION ---
Tick: ...
Position: ...
Facing: ...
HP: ...
Visible entities: ...
Legal actions: ...

Choose the single best action strictly from legal actions.
```

Important:

- Never dump raw SQL rows.
- Keep summaries concise.
- Do not include low-ranked memories.
- Do not include all memories.
- Always label memory type.

---

# 14. Proposed Python Modules

Recommended refactor:

```text
actors/
    agent.py
    models.py

memory/
    __init__.py
    models.py
    repository.py
    episodic.py
    semantic.py
    retrieval.py
    selector.py
    reflection.py
    context_builder.py

db/
    migrations/
        001_create_cat_episodes.sql
        002_create_cat_semantic_memories.sql
```

Possible responsibilities:

### `memory/models.py`

Pydantic/dataclass models:

```python
EpisodicMemory
SemanticMemory
MemoryCandidateEvent
EpisodicMemoryDecision
SemanticMemoryCandidate
MemoryRetrievalContext
```

### `memory/repository.py`

Postgres persistence:

```python
insert_episode(...)
query_episodes(...)
insert_semantic_memory(...)
update_semantic_memory(...)
query_semantic_memories(...)
get_related_episodes(...)
```

### `memory/retrieval.py`

Scoring and ranking only.

No prompt rendering.

### `memory/selector.py`

Gemini or deterministic memory candidate selection.

### `memory/reflection.py`

Episode → semantic consolidation.

### `memory/context_builder.py`

Combines all three lanes into the context injected into the cat decision prompt.

---

# 15. Database Access

Use an async Postgres library to avoid blocking the FastAPI/ADK event loop.

Recommended:

```text
SQLAlchemy 2.x async + asyncpg
```

or simpler:

```text
asyncpg directly
```

For this project, `asyncpg` is acceptable if ORM abstractions are unnecessary.

Environment variables:

```env
DATABASE_URL=postgresql://...
EPISODIC_TOP_K=3
SEMANTIC_TOP_K=3
MEMORY_SELECTION_ENABLED=true
REFLECTION_ENABLED=true
REFLECTION_INTERVAL_TICKS=50
```

---

# 16. Game Engine Event Interface

Prefer a structured event object.

Example:

```python
class GameEvent(BaseModel):
    game_id: str
    tick: int
    event_type: str
    actor_id: str
    target_ids: list[str] = []
    location: dict[str, int] | None = None
    payload: dict[str, Any] = {}
```

Examples:

```json
{
  "game_id": "game-42",
  "tick": 143,
  "event_type": "cat_attacked_me",
  "actor_id": "betsy",
  "target_ids": ["cat-1"],
  "location": {"x": 9, "y": 4}
}
```

The memory subsystem should consume these events.

Avoid coupling memory formation directly to dozens of arbitrary game-engine conditionals.

---

# 17. Memory Service Interface

Suggested abstraction:

```python
class CatMemoryService:
    async def process_events(
        self,
        cat_id: str,
        game_id: str,
        events: list[GameEvent],
    ) -> None:
        ...

    async def retrieve_for_decision(
        self,
        cat_id: str,
        game_id: str,
        obs: Observation,
    ) -> MemoryContext:
        ...

    async def reflect(
        self,
        cat_id: str,
        game_id: str,
        current_tick: int,
    ) -> None:
        ...
```

This keeps memory logic separate from the agent runner.

---

# 18. Avoid a Dedicated Memory Sub-Agent in V1

Do not deploy a second agent/runtime whose only task is memory management yet.

Reasons:

- doubles or increases LLM calls,
- increases latency,
- increases cost,
- makes debugging harder,
- obscures basic mechanics before they are understood.

Use ordinary application services plus occasional Gemini calls.

Later experiment:

```text
Cat Agent
   ↓
Memory Agent
   ├── insert memory
   ├── update memory
   ├── merge memory
   └── delete memory
```

But only after the primitive system works.

---

# 19. Embeddings / Vector Retrieval — V2

Do not block V1 on embeddings.

After metadata retrieval works, add `pgvector`.

New episode field:

```sql
ALTER TABLE cat_episodes
ADD COLUMN embedding vector(<dimension>);
```

Then embed episode summaries.

Query:

```text
current situation
   ↓
embedding
   ↓
nearest-neighbor search
   ↓
candidate memories
```

Then compare three retrieval approaches:

1. metadata SQL retrieval,
2. vector similarity retrieval,
3. hybrid retrieval.

Expected eventual architecture:

```python
hybrid_score = (
    0.35 * semantic_similarity
    + 0.20 * entity_overlap
    + 0.15 * spatial_relevance
    + 0.15 * recency
    + 0.15 * importance
)
```

Weights should be configurable and experimentally tuned.

---

# 20. Memory Scope

Every query/write must be scoped by at minimum:

```text
game_id
cat_id
```

This is critical.

Cat memories must not leak across cats unless explicitly designed.

Example:

```text
game-42 / cat-1
game-42 / betsy
game-42 / cat-3
```

Each should have independent beliefs and experiences.

Potential future choice:

- persist identity memories across games,
- reset world-specific memories per game.

For V1, scope by game + cat.

---

# 21. THINK Action Integration — Later in Today's Work if Time Allows

Long-term goal:

```text
normal movement action:
    current obs
    working memory
    top 2-3 episodic
    top 2-3 semantic
    → decision

think action:
    wider episodic retrieval
    wider semantic retrieval
    reflection/consolidation
    potentially larger Gemini reasoning budget
    → no physical move
    → cat busy for N ticks
```

This turns memory consolidation into an actual game mechanic.

Important:

- global game ticks continue,
- only the thinking cat is locked,
- overthinking has survival cost.

Possible future fields:

```python
busy_until_tick: int
current_action: str
```

---

# 22. Observability

Instrument this from the beginning.

Per cat decision, log:

```text
game_id
cat_id
tick

working_memory_count
episodic_candidates_count
episodic_retrieved_ids
semantic_retrieved_ids

approx_prompt_tokens
LLM_latency_ms
decision
strategy
target_mouse_id
```

Per memory write:

```text
memory_id
memory_type
source_event
importance
selection_reason
```

Per reflection:

```text
source_episode_ids
semantic memories inserted
semantic memories updated
confidence change
```

This data will later make it possible to compare:

```text
no memory
vs
SQL memory
vs
vector retrieval
vs
Google Memory Bank
```

---

# 23. Failure Handling

Memory infrastructure should not make the cat fail to act.

If Postgres retrieval fails:

```text
continue with:
skills.md
+ working memory
+ current observation
```

If episodic selection fails:

```text
skip memory write
```

If reflection fails:

```text
retain existing episodic memories
```

Memory must degrade gracefully.

The game loop should remain functional even if the memory subsystem is unavailable.

---

# 24. Transaction / Concurrency Considerations

Multiple cat requests may run concurrently.

Ensure:

- all memory writes are scoped by cat/game,
- repository methods are async,
- semantic consolidation updates use transactions where needed,
- duplicate semantic writes are handled safely.

Potential unique constraint for semantic beliefs:

```sql
CREATE UNIQUE INDEX uq_cat_semantic_key
ON cat_semantic_memories (
    game_id,
    cat_id,
    subject,
    predicate
);
```

Use upsert where appropriate.

---

# 25. Implementation Sequence

## Phase 1 — database + repository

Implement:

- Postgres connection.
- migrations.
- `cat_episodes`.
- `cat_semantic_memories`.
- repository CRUD.

Acceptance:

- can insert/read episode.
- can insert/update/read semantic memory.

---

## Phase 2 — event generation

Implement:

- structured `GameEvent`.
- candidate event detection.
- send events to memory service.

Acceptance:

- meaningful events generated.
- routine moves do not flood memory.

---

## Phase 3 — deterministic episodic writes

Initially avoid second LLM call.

Create concise summaries from event templates.

Example:

```python
"mouse_escaped" ->
"Mouse {mouse_id} escaped through {burrow_id} after a chase."
```

Acceptance:

- memories accumulate without per-tick explosion.

---

## Phase 4 — retrieval

Implement metadata-based retrieval:

- entity match,
- location,
- recency,
- importance.

Acceptance:

- `top_k` relevant episodes returned.
- prompt size remains bounded.

---

## Phase 5 — prompt integration

Add sections:

```text
RELEVANT EPISODIC MEMORIES
RELEVANT LEARNED BELIEFS
```

to `query_adk_gemini`.

Acceptance:

- cat decision uses retrieved memory.
- no session-history growth.

---

## Phase 6 — LLM memory selection

Add Gemini-based decision for candidate events.

Batch events if possible.

Acceptance:

- low-value candidate events may be discarded.
- summaries become first-person / perspective-specific.
- importance scores come from model.

---

## Phase 7 — semantic reflection

Implement:

- retrieve related episodes,
- call Gemini,
- write/update semantic memory.

Trigger periodically first.

Acceptance:

- repeated experiences create generalized beliefs.

---

## Phase 8 — THINK integration

Move reflection into explicit cognition.

Acceptance:

- THINK consumes ticks.
- reflection may alter semantic memory.
- physical movement pauses only for that cat.

---

## Phase 9 — embeddings

Add pgvector and compare:

- SQL metadata retrieval.
- vector retrieval.
- hybrid retrieval.

---

## Phase 10 — managed platform comparison

Only after the primitive system is understood:

- deploy ADK agent to Google Agent Runtime,
- test Google Memory Bank,
- compare its extraction/retrieval against custom implementation.

---

# 26. Explicit Non-Goals for V1

Do not implement yet:

- Kubernetes.
- Google Agent Runtime.
- Google Memory Bank.
- dedicated vector database.
- separate memory agent.
- Pub/Sub.
- multi-region storage.
- complex event sourcing.
- full knowledge graph.
- automatic personality models.
- deterministic tactical policy.

Keep focus on understanding memory mechanics.

---

# 27. Expected Cat Decision Flow After V1

```text
GAME SERVER
    │
    │ observation + events
    ▼
CAT AGENT SERVICE
    │
    ├─ working history[-3:]
    │
    ├─ episodic retrieval ──────► PostgreSQL
    │
    └─ semantic retrieval ──────► PostgreSQL
                  │
                  ▼
            context builder
                  │
                  ▼
skills.md
+ working memory
+ episodic memories
+ semantic memories
+ current observation
                  │
                  ▼
                Gemini
                  │
                  ▼
               action
                  │
                  ▼
             game engine

Meanwhile:

game events
    │
    ▼
memory candidate detection
    │
    ▼
episodic memory selection/write
    │
    ▼
PostgreSQL

Occasionally:

episodes
    │
    ▼
reflection
    │
    ▼
semantic beliefs
```

---

# 28. Critical Architectural Distinctions

Keep these distinctions explicit in code and naming.

## Working memory ≠ episodic memory

Working memory is recent high-fidelity context.

Episodic memory is selected durable experience.

---

## Episodic memory ≠ semantic memory

Episode:

```text
"Betsy attacked me at tick 114."
```

Semantic belief:

```text
"Betsy is hostile toward me."
```

---

## Database storage ≠ model context

The database can contain unlimited memory.

Only selected memories enter a given Gemini call.

---

## RAG/retrieval ≠ memory formation

Retrieval answers:

```text
"What stored memories are relevant now?"
```

Memory formation answers:

```text
"What should be stored in the first place?"
```

---

## Reflection ≠ ordinary action decision

Action decision:

```text
"What should I do now?"
```

Reflection:

```text
"What have I learned from what happened?"
```

These may use the same Gemini model but are separate cognitive operations.

---

# 29. Core Success Criteria

This implementation is successful when:

1. A cat can run for hundreds or thousands of ticks without prompt growth.
2. Recent tactical context remains available through working memory.
3. Important past experiences persist beyond the 3-tick window.
4. Only a few relevant memories are injected per decision.
5. Repeated experiences can become durable semantic beliefs.
6. Different cats can form different memories from different experiences.
7. The LLM remains responsible for actual tactical strategy.
8. Memory failures do not stop the game loop.
9. Every memory retrieval/write/reflection is observable for debugging.
10. The architecture is simple enough that later migration to Agent Runtime / Memory Bank can be compared against something we fully understand.

---

# 30. Recommended First Coding Task

Start with the smallest end-to-end slice:

```text
Postgres schema
    ↓
one candidate event: mouse_seen
    ↓
write one episodic memory
    ↓
retrieve top 2 episodes for a cat
    ↓
inject them into query_adk_gemini()
```

Do not begin with semantic memory or Gemini-based memory selection.

Once one episode can survive beyond the 3-tick history and later reappear in the cat's context, the fundamental architecture is proven.

Then expand deliberately.
