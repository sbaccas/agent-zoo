# 📌 Active Feature & Optimization Board (`features.md`)

> See also the full historical backlog in [`docs/FEATURE_BACKLOG.md`](docs/FEATURE_BACKLOG.md).

---

## 🎮 Gameplay & Mechanics Investigations

### 1. `GAME-05`: `cat-3` (Evil Cat) Score & Kill Accounting Bug
- **Status**: 📋 To Investigate
- **Files**: [`app/game/scheduler.py`](app/game/scheduler.py#L304-L336), [`app/game/stats.py`](app/game/stats.py#L273-L337)
- **Summary**:
  - For regular cats (`cat-1`, `cat-2`, NPCs), every mouse or cat elimination increments `kills += 1` and adds `1 + absorbed_score` to `score` (`score = kills + absorbed_kills`).
  - For Evil Cat (`cat-3`), mouse kills give `0` kills and `0` score, while rival cat eliminations increment `stats.kills += 1` (`stats.py:275`) but `scheduler.py:323` sets `earned_score = absorbed_score` (omitting the `+1` for the cat kill itself) and `stats.py:336` sets `cat_score = s.absorbed_kills` (ignoring `s.kills`).
  - Result: Boxscore displays `kills: 3, absorbed_kills: 187, score: 187` (instead of `190`), and killing a rival cat with `0` mouse kills increments `kills` while keeping `score` at `0`.

### 2. `GAME-06`: HP, Damage & Healing Settings Review
- **Status**: 📋 To Review & Optimize
- **Files**: [`app/game/entities.py`](app/game/entities.py), [`app/game/ws_manager.py`](app/game/ws_manager.py#L120-L126), [`app/game/scheduler.py`](app/game/scheduler.py#L340-L348)
- **Current Baseline**:
  - Regular Cats: `100` Max HP, `34` Attack Power (`3` slashes to kill at full HP).
  - Evil Cat (`cat-3`): `150` Max HP, `34` Attack Power (`5` slashes to kill at full HP).
  - Healing: `+40 HP` on mouse capture, `+100 HP` on rival cat elimination (capped at `max_hp`).

---

## ⚡ Agent & LLM Optimization Board

### 3. `OPT-05`: Eliminate Double PNG Viewport Render in `cat-3` Actor
- **Status**: ✅ Implemented (Safe / Zero Behavioral Change)
- **Target**: [`actors/evil_cat/actor.py`](actors/evil_cat/actor.py#L627-L715)
- **Summary**: Cache `_viewport_png_bytes` inside `capture` during `build_decision_prompt` and reuse those bytes directly in `query_adk_gemini` instead of rendering the 260×260 PIL PNG a second time on every `cat-3` tick.

### 4. `OPT-03`: Deduplicate Triplicated History in `cat-3` Coach Prompt (`build_coach_eval_prompt`)
- **Status**: ✅ Implemented (Kept Section 3 `LATEST ACTOR PAYLOAD` as Single Source of Truth)
- **Target**: [`actors/evil_cat/eval.py`](actors/evil_cat/eval.py#L945-L1085), [`actors/evil_cat/actor.py`](actors/evil_cat/actor.py#L494-L500)
- **Summary**: Removed the redundant `last_10_history_block` (Section 1) and `last_10_observations_block` (Section 2) from `build_coach_eval_prompt`, enriched `build_decision_prompt`'s 25-tick history lines with `HP: {hp}/{max_hp}`, and appended the current tick's subordinate decision to `latest_actor_payload_block` (Section 3), saving ~2,000–3,000 duplicate input tokens per Evil Coach call.

### 5. `OPT-01`: Coach Thinking Budgets (`Call 1` vs. `Call 2` Allocation)
- **Status**: ⏸️ Deferred / Needs Independent Tuning
- **Target**: [`actors/cat/subagents/eval.py`](actors/cat/subagents/eval.py), [`actors/evil_cat/eval.py`](actors/evil_cat/eval.py)
- **Lesson Learned**: In the 2-call Coach loop, Call 1 only selects tools (`get_boxscore`, `get_coverage_map`, `get_event_reel`), whereas Call 2 receives the tool outputs and synthesizes the standing directive. Capping Call 2 at `512` thinking tokens starved the Coach of reasoning depth right when the boxscore/heatmap arrived. Any future budget step-down should cap Call 1 (tool selection), not Call 2 (directive synthesis).

### 6. `OPT-02`: Down-Tick (`action_cooldown > 0`) LLM Call Skipping & WebSocket Sync
- **Status**: ⛔ High Risk — Deferred (Do Not Enable Without Full Sync Redesign)
- **Target**: [`actors/cat/api.py`](actors/cat/api.py), [`actors/evil_cat/api.py`](actors/evil_cat/api.py)
- **Lesson Learned**:
  - Skipping all locked ticks (`cd=2`, `cd=1`) in `decoupled=True` mode caused the Actor to consume pre-move decisions from the prior `FREE` tick (`cd=0`), introducing a 1-move coordinate lag and post-kill double swings (`sim-d38a998f`).
  - Pre-warming on `cd=1` fixed own-position lag (`sim-5c9ad8d2`), but because `WsClientManager._deliberate_and_reply` answers `can_act=True` (`cd=0`) observations in 0–1ms using the `cd=1` background decision instead of deliberating on the fresh `cd=0` board within the 4.0s `budget_sec` window, a 1-tick target movement lag remained.

### 7. `OPT-04`: Trimming `actor_skills.md` & `eval_skills.md`
- **Status**: ⛔ Rejected / Dangerous — Do Not Bulk-Trim
- **Target**: [`actors/cat/actor_skills.md`](actors/cat/actor_skills.md), [`actors/cat/subagents/eval_skills.md`](actors/cat/subagents/eval_skills.md)
- **Lesson Learned**: Aggressive condensation (23.7 KB $\to$ 7.9 KB and 30.0 KB $\to$ 6.8 KB) stripped worked examples and coaching anti-patterns, causing lunge share to collapse and Coaches to issue self-contradictory boundary rules.