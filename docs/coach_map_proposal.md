# Coach Map + Knowledge Tiers — Proposal v2

Premise validated in v1: ignored sectors hold **3.02x more mice**, 79% of the
surviving population sits in the five sectors the cats never entered. Driver is
permanent depletion (mice never respawn), not avoidance.

---

## 1. The knowledge tiers

The rule you're describing, made explicit:

| Tier | Who | Epistemic rule | Gets |
| --- | --- | --- | --- |
| **1** | **Actor** | Only what a creature on the board could perceive *this tick* | position, facing, cooldowns, entities in FOV, own capabilities, legal actions |
| **2** | **Coach** | Anything derivable from the *complete record of play*, plus the scoreboard | full transcript, box score for **all** cats, (new) coverage map, (new) population counts |
| **3** | **Server** | Ground truth | every mouse position, RNG, the whole board |

**The rule: any datum crossing a boundary needs a toggle**, so an arm can be run
with and without, and the archive can say which arm it was.

### Classifying what's on the table

| Datum | Native tier | Proposed for | Verdict |
| --- | --- | --- | --- |
| Coverage map (cat's own positions) | 1 (its own memory) | Coach | ✅ Aggregate of its own history — fair |
| `mice_alive`, `captures` | 3 | Coach | ⚠️ **Promotion** — toggle it |
| Rival cat's slash accuracy | 3 | Coach | ✅ *Already promoted today* — precedent exists |
| Mouse positions / density | 3 | nobody | ❌ Never. Destroys the experiment |
| Coverage map → **actor** | 1 | Actor | 🔸 See below |

> [!NOTE]
> The box score **already** hands the coach Tier-3 data: the rival's internal
> counters, which cat-1 has no way to observe. So `mice_alive` is a *smaller*
> promotion than one you already shipped. It's not a new kind of concession,
> just a new instance — but it should still get a switch.

### The interesting edge case you didn't ask about

The cat's **own trail** is the one piece of map-like data that is arguably
legitimate for the **actor**. It isn't omniscience, it's *memory* — a real animal
knows where it has been. Everything else in Tier 2 is privileged; this isn't.

I'm **not** proposing it now: you want the inference to happen in the coach, and
handing the actor a map would confound that. But it's the natural second arm, so
I'd build the toggle to accommodate it rather than hard-coding "coach only".

---

## 2. Toggles

A `DisclosureConfig` block on `SimulationConfig`, surfaced in settings:

```
coach_sees_population      default ON    mice_alive / captures in the box score
coach_sees_coverage_map    default ON    the get_coverage_map tool returns data
actor_sees_own_trail       default OFF   reserved for the second arm
```

Recorded in the **archive manifest**, which already captures `settings`. That is
what makes two runs comparable after the fact — see §5, it also settles the aim
assist question.

---

## 3. The tool — confirmed

`get_coverage_map()`, separate from `get_boxscore`, wording **A** as you said:

> "Call `get_coverage_map` to see where on the board the cat has spent its ticks."

Returns `ascii` (the 5x5 digest, both cats), `arena`, and — per your note —
**population goes in the box score instead**, since that's where game-wide
counters already live. So `mice_alive` / `mice_initial` joins `get_boxscore`, and
the coach combines two tools it already has reason to call. Nothing new reaches
the actor.

**Success criterion, your words:** a note of the form

> "Patrol the western third (x < 33)"

That's executable blind, because the actor's observation carries its own
coordinates. "Head for the least-visited region" is not.

---

## 4. Tool-call telemetry

You're right that this is needed, and it's more than housekeeping: for this
experiment **"did the coach call the tool?"** is the primary observable. Without
it, a null result is unattributable — you can't tell "looked and inferred
nothing" from "never looked".

Cheap, because the plumbing exists:

- [The evaluate loop](file:///Users/shanebaccas/Desktop/workspace/C+M/actors/cat/subagents/eval.py#L1088-L1093) already walks ADK
  event parts. Tool calls arrive as `function_call` / `function_response` parts,
  which the current loop skips (it filters on `p.text`). Capturing them is a few
  lines inside the loop that already runs.
- [`_last_eval_telemetry`](file:///Users/shanebaccas/Desktop/workspace/C+M/actors/cat/subagents/eval.py#L607-L612) already
  carries latency and thinking spend to
  [api.py's `last_run`](file:///Users/shanebaccas/Desktop/workspace/C+M/actors/cat/api.py#L1460), which
  [workbench.js renders](file:///Users/shanebaccas/Desktop/workspace/C+M/actors/cat/static/workbench.js#L1535-L1546). Add
  `tool_calls: [{name, ok}]` and render beside the existing budget readout.

Per-evaluation, not cumulative, matching how `last_run` already behaves.

---

## 5. Aim assist → ON by default

Straightforward, with two things to be aware of.

**It's encoded in three places, not one:**

| File | |
| --- | --- |
| [models/simulation.py:85](file:///Users/shanebaccas/Desktop/workspace/C+M/app/models/simulation.py#L85-L92) | the real default |
| [settings.js:197](file:///Users/shanebaccas/Desktop/workspace/C+M/app/static/settings.js#L194-L197) | "Reset to defaults" |
| [app.js:1756](file:///Users/shanebaccas/Desktop/workspace/C+M/app/static/app.js#L1756) | the same, in the main window |

> [!WARNING]
> Changing only the model default leaves both **Reset** buttons setting the box
> back to *unchecked* — so the UI would silently disagree with the server about
> what "default" means. All three move together.

**The existing comment argues against you, and it's out of date.** It says:

> Turning it on by default would silently invalidate that baseline.

But [archive.py](file:///Users/shanebaccas/Desktop/workspace/C+M/app/game/archive.py#L268-L272) already records the live
`species` config — including `aim_assist` — in every run's manifest. Comparability
is preserved by the *manifest*, not by the default. The fear the comment encodes
was addressed when archiving shipped.

So: flip it, and rewrite that comment rather than leave it contradicting the
code. You committed "fixed doc drift" this morning; this is the same class of
thing.

> [!NOTE]
> Your live game already runs with `aim_assist: True` (coaching note 11 confirms
> it), so this changes the shipped default to match what you're actually playing.

---

## 6. Work items

| # | Item | Size |
| --- | --- | --- |
| 1 | Aim assist default ON, all three sites + comment rewrite | XS |
| 2 | `DisclosureConfig` + settings UI + manifest recording | S |
| 3 | `mice_alive` / `mice_initial` into the box score payload | XS |
| 4 | `get_coverage_map()` ADK tool + one line in `eval_skills.md` | S |
| 5 | Tool-call telemetry: capture, expose, render | S |

1 and 3 are independent and safe to land first. 4 is the experiment. 5 is the
instrument for 4 — worth having *before* you run it, or the first run is
unmeasurable.

---

## Open questions

1. **Order** — land 1+3+5 first so the dashboard is instrumented, then 4 as a
   clean cut-over? Or all at once?
2. **`DisclosureConfig` placement** — `SimulationConfig` (game-wide, archived) as
   proposed, or on the actor side where the coach's other switches live
   (`CAT_AGENT_BOXSCORE_FETCH` is an env var)? Game-side gets it into the
   manifest for free, which I think decides it.
3. **Does the coach need to know mice never respawn?** Still leaning no — it's
   most of the inference. But it *is* a rule of the game rather than a hint, and
   a human player would be told it.
