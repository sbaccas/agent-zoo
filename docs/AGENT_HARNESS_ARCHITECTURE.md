# 🧠 Universal Agent Harness Architecture

**Specification Date:** October 8, 2026  
**Document Version:** 1.0.0  
**Status:** Approved Architectural Standard  

---

## 1. Executive Summary & Core Principle

The **Agent Zoo Playground** is built upon a fundamental architectural axiom:

> **All autonomous agents—regardless of animal species, team faction, tactical alignment, or behavioral personality—share the exact same underlying Python execution harness. Agents are distinguished EXCLUSIVELY by their injected skills manuals.**

There is **zero code branching** or hardcoded species/personality logic inside the core Python execution loop. Whether an agent is an innocent mouse foraging in grass, a standard cat hunting mice, an apex juggernaut assassin (Evil Cat) absorbing bounties, or a future hunting hound, they all run through the **identical dual-consciousness execution engine**.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 UNIVERSAL AGENT EXECUTION HARNESS (Python)                 │
│                                                                             │
│   ┌───────────────────────────────┐     ┌───────────────────────────────┐   │
│   │    FAST-BRAIN TACTICAL ACTOR  │     │   SLOW-BRAIN STRATEGIC COACH  │   │
│   │   (System 1 / Reflex + ADK)   │     │    (System 2 / Long-Horizon)  │   │
│   │  • Low-latency turn loop      │     │  • Telemetry & A2A Diplomacy  │   │
│   │  • 3-tick sliding context     │     │  • Evaluates every N ticks    │   │
│   │  • Microsecond reflex fallback│     │  • Out-of-band deliberation   │   │
│   └───────────────▲───────────────┘     └───────────────▲───────────────┘   │
└───────────────────┼─────────────────────────────────────┼───────────────────┘
                    │                                     │
      [ Boot-Up Skills Injection ]           [ Boot-Up Skills Injection ]
                    │                                     │
       ┌────────────▼──────────┐             ┌────────────▼──────────┐
       │   ACTOR SKILLS FILE   │             │   COACH SKILLS FILE   │
       │     (skills.md)       │             │  (coach_skills.md)    │
       │                       │             │                       │
       │ • actor_skills.md     │             │ • eval_skills.md      │
       │ • evil_skills.md      │             │ • coach_skills.md     │
       │ • mouse_skills.md     │             │ • mouse_coach_skills.md│
       │ • hound_skills.md     │             │ • hound_coach_skills.md│
       └───────────────────────┘             └───────────────────────┘
```

---

## 2. Boot-Up Skills Injection: Decoupled Logic & Persona

On process startup, each agent container or workbench process is configured with paths to its relevant skills files:

| Environment Variable | Description | Default Fallback |
| :--- | :--- | :--- |
| `CAT_SKILLS_PATH` / `AGENT_SKILLS_PATH` | Path to the Tactical Actor's skills handbook | `actors/cat/actor_skills.md` |
| `EVAL_SKILLS_PATH` / `COACH_SKILLS_PATH` | Path to the Strategic Coach's advisory manual | `actors/cat/subagents/eval_skills.md` |

### Path Resolution Rules
1. **Explicit Environment Configuration**: If `CAT_SKILLS_PATH` or `EVAL_SKILLS_PATH` is passed on startup (e.g. via launch scripts, Dockerfiles, or orchestrators), that path is authoritative.
2. **Convention Resolution**: If no explicit path is set, the harness resolves conventions by agent identifier (`{agent_id}_skills.md` and `{agent_id}_coach_skills.md`).
3. **Canonical Default**: Defaults to the standard reference implementation (`actor_skills.md` and `eval_skills.md`).

This guarantees that introducing a new species (e.g., `hound-1` or `mouse-commander`) requires **only writing new Markdown skill files**, without modifying a single line of Python harness code.

---

## 3. Dual-Brain System-1 / System-2 Anatomy

Each agent operates as a synchronized dual-brain intelligence:

### 1. Fast-Brain Tactical Actor (System 1)
- **Role**: Tick-by-tick tactical execution under strict game clock latency constraints (100ms–500ms per tick).
- **Cognitive Loop**: Bounded 3-tick sliding context window, keeping prompt size capped (~250 tokens).
- **Reflex Circuit Breaker**: Evaluates deterministic in-memory heuristics if LLM deliberation is in flight or deadline is exceeded, guaranteeing the agent never misses a turn or freezes the server.
- **Guidance**: Consumes `--- EVALUATOR NOTES ---` published by its Strategic Coach.

### 2. Slow-Brain Strategic Coach (System 2)
- **Role**: Macro-strategic advisor running out-of-band every $N$ ticks (default 5–50 ticks).
- **Cognitive Loop**: Ingests whole-game box scores, spatial coverage heatmaps, combat event reels, and recent transcripts.
- **Inter-Agent Diplomacy (A2A)**: Conducts strategic communications, negotiations, sector partitions, and radio deception over the Agent-to-Agent protocol.
- **Output**: Writes updated 6-pillar standing directives into `coaching_notes.md`.

---

## 4. The Dual-Channel Memory Model

To prevent cognitive pollution and maintain long-horizon coherence, the Strategic Coach maintains two strictly separated communication channels:

```
┌────────────────────────────────────────────────────────────────────────┐
│ CHANNEL 1: COACH PRIVATE NOTES (Tools: write_coach_note / read_coach_notes)│
│ Audience: YOUR FUTURE SELF ONLY (Internal Consciousness)               │
│ Purpose: Providing context to your future self in planning &           │
│          deliberating long-term strategies and conspiracies.           │
│ Scope: Hypotheses, treaty tracking, deceit logs, enemy patterns.       │
│ RESTRICTION: NEVER write standing orders or cat commands here!         │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ (Private deliberation across cycles)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ CHANNEL 2: PUBLISHED STANDING ORDERS (Output -> coaching_notes.md)     │
│ Audience: THE FRONTLINE OPERATIVE (Field Actor)                        │
│ Purpose: Operational tactical instructions executed turn-by-turn.      │
│ Scope: 6-Pillar operational structure (Objective, Combat Trigger,      │
│        Pursuit/Mobility, Spatial/Ambush, Deception Script, Heal).      │
│ RESTRICTION: Must be actionable, quantified imperatives for the actor. │
└────────────────────────────────────────────────────────────────────────┘
```

### Channel 1: Coach Private Notes (`write_coach_note` & `read_coach_notes`)
- **Audience**: **Your Future Self Only.** The frontline Actor never sees or reads this journal.
- **Purpose**: A persistent scratchpad to provide context across evaluation cycles. The Coach writes down deductions, hypotheses, deception logs (which lies or treaties were pitched to rival coaches), trap designs, and opponent psychological profiles.
- **Strict Prohibition**: **Never write standing orders or operational commands in private notes.** Confusing private notes with standing orders starves the Coach's future self of strategic context.

### Channel 2: Published Standing Orders (`coaching_notes.md`)
- **Audience**: **The Frontline Actor.** Read on every tick under `--- EVALUATOR NOTES ---`.
- **Purpose**: High-priority operational directives that guide real-time action selection without micro-managing legal turns.

---

## 5. Multi-Species & Personality Roadmap

As new animal types and behavioral archetypes are introduced into the Agent Zoo, they seamlessly plug into this harness:

| Agent Archetype | Fast-Brain Skills File | Slow-Brain Skills File | Distinguishing Mechanics |
| :--- | :--- | :--- | :--- |
| **Standard Cat** | `actor_skills.md` | `eval_skills.md` | Precision mouse hunter, 100 HP, territorial patrol |
| **Evil Cat (Assassin)** | `evil_skills.md` | `coach_skills.md` | 150 HP, 50% mouse heal, 2x rival bounty absorption, deception |
| **Autonomous Mouse** | `mouse_skills.md` | `mouse_coach_skills.md` | Forager/evader, burrow concealment, warning calls |
| **Hunting Hound** *(Future)* | `hound_skills.md` | `hound_coach_skills.md` | Pack tracker, scent trails, cooperative cornering |
| **Arena Neutral** *(Future)* | `neutral_skills.md` | `neutral_coach_skills.md` | Environmental scavenger, reactive hazards |

All agents run on the exact same FastAPI endpoints (`POST /act`), the same WebSocket streaming protocol, the same A2A messaging transport, and the same dual-brain harness.
