# Investigation Subsystem PRD

> Companion to `backend/PRD.md`. Adds a fourth subtask type — `investigate` — that lets the LLM run small discovery queries against the database before committing to an answer SQL query.

---

## 1. Overview

Today the planner sees only the static schema (table names, column types, table notes) before generating SQL. When a question hinges on knowing the actual values in a column — enums like `orders.status`, categories like `products.category`, the date range of a fact table, whether a join key has matches — the model has to guess. Wrong guesses produce empty result sets, and the SQL retry loop just guesses differently, often arriving at the same wrong assumption with new syntax.

The investigation subsystem gives the planner a way to spawn small read-only "look at the data" queries (`SELECT DISTINCT`, `COUNT(*)`, `MIN`/`MAX`, `SELECT ... LIMIT 10`), feed the results back into a second planning round, and only then commit to the answering query.

It is **opt-in per question**, decided by the LLM. The Python code does not detect "this looks like an enum question" and force-inject a discovery query — that would violate "LLM owns reasoning, not Python" (see `CLAUDE.md`).

---

## 2. Goals & Non-Goals

**Goals**
- Let the planner decide, per question, whether it needs to peek at data before answering.
- Reuse the existing SQL pipeline (validator, executor, safety) end-to-end — no parallel infrastructure.
- Reuse the existing planner re-plan loop. Round 1 = investigate, round 2 = answer.
- Hide investigation from the user. The synthesizer does not narrate "we ran SELECT DISTINCT…" in the final answer.
- Stay cheap. Investigation queries should be small (DISTINCT/COUNT/sample) and capped tighter than answer queries.

**Non-Goals**
- Investigation for `docs` / `lineage` subtask types. They have their own retrieval; SQL discovery doesn't help them.
- Cross-session caching of investigation results. Per-turn, in-graph state only.
- A separate "investigation budget" config knob. Reuse `max_sql_retries` and the existing 4-subtask cap.
- Letting investigation produce charts. They are scaffolding, not answers.

---

## 3. End-to-End Flow

```
planner (round 1)
    │
    ├── decides: "I need to know what statuses exist"
    │
    ▼
spawn investigate subtask
    │
    ▼
investigate_subtask_runner
    │   sql_generator (with "this is investigation" nudge)
    │   validator           (same as answer)
    │   executor            (tighter max_rows; lower retry budget)
    │
    ▼
subtask_join → re-plan
    │
    ▼
planner (round 2, sees investigation results in summary)
    │
    ├── round-2 prompt: "you may NOT spawn more investigate subtasks"
    │
    ▼
spawn sql subtask using actual values
    │
    ▼
sql_subtask_runner → synthesizer (skips investigate subtasks) → answer
```

The `merge_subtasks_by_id` reducer already handles the dual-round subtask list, so no state-shape changes are required beyond a string-literal addition.

---

## 4. Why Planner-Level, Not SQL-Runner-Level

Two designs were considered:

1. **Investigation as a planner-spawned subtask** (chosen).
2. **Investigation nested inside the SQL subtask runner** (rejected) — the SQL runner would, before generating the answer, optionally run a discovery query of its own choosing.

The runner-level design avoids round-budget pressure but splits the decision of *what to look at* across two prompts: the planner picks the question, the runner picks the discovery angle. Planner-level keeps the decision in one place, lets discovery results shape *which* answer subtasks get spawned (maybe none — maybe a docs lookup is more appropriate after seeing the data), and produces a clean, auditable subtask trail. The two-rounds-is-enough math (round 1 investigate, round 2 answer) makes the budget non-binding in practice.

**Why not auto-inject discovery at `context_gatherer` time?** That's the Python-owns-reasoning anti-pattern: writing rules like "always SELECT DISTINCT on every TEXT column with low cardinality" bloats every prompt, runs queries the LLM may not need, and short-circuits the model's own judgment.

---

## 5. State Schema Changes

**File:** `backend/app/graph/state.py`

`SubtaskResult.type` literal: extend from `"sql" | "docs" | "lineage"` to `"sql" | "docs" | "lineage" | "investigate"`.

No new fields. Investigation reuses the existing SQL-shaped fields:
- `generated_sql` — the discovery query.
- `raw_data` — the discovery query's result set.
- `validation_error`, `execution_error`, `sql_attempts` — same retry signal as a normal SQL subtask.

The `merge_subtasks_by_id` reducer already merges by id; no reducer change.

---

## 6. Planner Prompt Changes

**File:** `backend/app/graph/nodes/planner.py`

### 6.1 System prompt (L13–69)

Add a fourth subtask type. Suggested wording:

> - `investigate` — Use when you need to look at actual data values before writing the answering query. Examples: you don't know what values live in an enum-like column; you need the date range of a table; you want to confirm a join key has matches. Investigation queries must be small: `SELECT DISTINCT col FROM t LIMIT 50`, `SELECT COUNT(*) FROM t`, `SELECT MIN(x), MAX(x) FROM t`, or `SELECT * FROM t LIMIT 10`. Investigation is **never** the final answer.

Reinforce the existing "prefer 1 subtask" rule by adding: "If you spawn an `investigate` subtask, do not also spawn an `sql` subtask in the same round — wait for the investigation to come back first."

### 6.2 Round-2 prompt enhancement (L199–208)

In round 2 (when `_summarize_completed_subtasks` is rendered), add a hard line:

> You have already used your investigation budget. You may NOT spawn `investigate` subtasks this round. Either spawn the answering subtask(s) or set `ready_to_answer: true`.

### 6.3 Re-plan summary (L133–167)

`_summarize_completed_subtasks` already renders SQL-shaped subtask results as plain text (columns + first N rows). Confirm it does not filter by `type` — investigation results are exactly what the planner needs to see in round 2. If the function currently filters to `type == "sql"`, broaden it to include `"investigate"`.

### 6.4 Round budget

Keep `MAX_PLANNING_ROUNDS = 2` (L170). Round 1 investigates, round 2 answers. Bump to 3 only if the user starts asking questions that need investigation followed by multi-subtask answers — measure first.

---

## 7. Investigate Subtask Runner

**File:** `backend/app/graph/nodes/subtask_runners.py`

Add `investigate_subtask_runner` modeled on `sql_subtask_runner` (L43–110), with three differences:

1. **No visualizer step.** Investigation outputs aren't charted.
2. **Tighter `max_rows`.** 50 rows is plenty for DISTINCT / sample queries. Either pass an override to the executor or wrap the executor in a closure with the lower cap. The 30s timeout stays the same.
3. **Lower retry budget.** `max_retries = 1`. Investigations that don't run on the second try aren't worth a third — re-planning is cheaper than thrashing.

`sql_generator_node`, `validator_node`, `executor_node` are reused as-is — they don't care about subtask `type` once the prompt nudge is in place.

Wire the runner into `workflow.py` (L24–28 area):
- Add `"investigate": "investigate_subtask_runner"` to the type → node-name map.
- Register the node and add `investigate_subtask_runner → subtask_join` edge.

---

## 8. SQL Generator: Type-Aware Prompt Nudge

**File:** `backend/app/graph/nodes/sql_generator.py`

Branch on `current_subtask["type"]` in the prompt construction (L46–107). For `type == "investigate"`, append to the user message:

> This is an **investigation query**, not the final answer. Write ONE small SELECT that surfaces the values, range, or count needed to answer the user's question later. Prefer:
> - `SELECT DISTINCT col FROM t LIMIT 50`
> - `SELECT COUNT(*) FROM t WHERE ...`
> - `SELECT MIN(col), MAX(col) FROM t`
> - `SELECT * FROM t LIMIT 10`
>
> Do NOT attempt to answer the user's question here. Do NOT compute aggregations beyond simple COUNT/MIN/MAX. The result will inform a follow-up query.

Keep all the existing SQLite retry hints (`::cast`, `DATE_TRUNC`, `EXTRACT`, `NOW`/`INTERVAL`, `:param`, etc. — see `CLAUDE.md`). Investigation queries hit the same dialect quirks.

---

## 9. Synthesizer: Hide Investigation From Final Answer

**File:** `backend/app/graph/nodes/synthesizer.py`

In `_format_subtask` (L47–109), skip subtasks where `type == "investigate"`. The synthesizer renders only `sql`, `docs`, `lineage`. Investigation is scaffolding — the user should never see "we first checked DISTINCT statuses" in the answer.

If every successful subtask in the final state is an `investigate` (i.e. round 2 produced no answer), the synthesizer already has a fallback path for "no usable results" — confirm it triggers cleanly here.

---

## 10. Strategist: Don't Suggest Follow-Ups Off Investigations

**File:** `backend/app/graph/nodes/strategist.py`

The current filter at L57–59 selects subtasks with `raw_data`. Tighten to `raw_data AND type == "sql"` so investigation results don't leak into follow-up suggestions. (An investigation like "DISTINCT status values" is not a useful seed for "what other questions might you ask?")

---

## 11. Safety, ACL, and Execution Guardrails

Investigation queries flow through **the same** safety validator and table-ACL check as answer queries. Specifically:

- **Safety regex** (`backend/app/sql/safety.py`): blocks INSERT/UPDATE/DELETE/DROP/etc. — applies to investigation unchanged.
- **Table ACL** (`validator_node`): investigation queries must reference tables in `context.visible_tables`, with the existing CTE-name exclusion (`_CTE_NAME_PATTERN`).
- **`PRAGMA query_only = ON`** at the engine level — applies to all queries.
- **Timeout** stays 30s.
- **Row cap** is the single deliberate divergence: 50 rows for investigation vs. 500 rows for answer queries. Implement via a parameter override at the runner level, not a global config change.

A re-implementer who skips the validator/ACL on investigation queries because "it's just discovery" has reintroduced a footgun. Investigation queries are still SQL the LLM wrote against a user-scoped context — they get the same checks.

---

## 12. Token Accounting

Investigation adds at minimum one extra LLM call per question that uses it (planner round 2 + sql_generator for the investigation). The `token_usage` field already uses an `add` reducer (`Annotated[list, add]`) so parallel/sequential subtask runs concatenate cleanly. Confirm the new runner aggregates `token_usage` from each step (generator → validator → executor) the same way `sql_subtask_runner` does at L59.

A turn that uses investigation pays for: planner round 1 + investigate generator + planner round 2 + sql generator + (validator/executor are not LLM calls) + synthesizer + strategist. Roughly 1.5–2× a non-investigating turn.

---

## 13. Failure Modes

| # | Failure | Behavior |
|---|---|---|
| 1 | Planner spawns an investigation in round 1 that fails validation/execution after 1 retry | Subtask marked completed with errors. Round 2 planner sees the failure in the summary and either retries via a different investigation (NOT allowed — round 2 forbids investigate), commits to a best-guess SQL, or sets `ready_to_answer: false` and falls through to synthesizer's empty-results path. |
| 2 | Investigation succeeds but returns 0 rows (e.g. `SELECT DISTINCT status FROM orders` on an empty table) | Empty `raw_data` is rendered to round-2 planner. Planner should infer "no data" and answer accordingly. |
| 3 | Round-1 planner spawns both `investigate` and `sql` simultaneously | Prompt rule (§6.1) says don't. If the LLM violates it anyway, both run in parallel — the SQL answer query proceeds without seeing the investigation. Acceptable degradation; do not enforce in code. |
| 4 | Round-2 planner tries to spawn another `investigate` despite the prompt rule | Allow it through (the type is valid) but flag in logs. If this happens often, escalate the round-2 prompt or add a Python-side filter — but only after measuring. |
| 5 | All subtasks in final state are `investigate` (synthesis has nothing to render) | Synthesizer's existing empty-state path handles it. The user gets "I couldn't find data to answer that" rather than a chart of DISTINCT values. |
| 6 | Investigation query times out (30s) | Same as any SQL timeout — caught by executor, surfaces as `execution_error`, retry-once-then-give-up. |

---

## 14. Non-Obvious Behaviors

These are the things that bite re-implementers.

1. **Investigation is opt-in, decided by the LLM.** Do not add Python heuristics that auto-spawn investigation for "enum-shaped" columns. The whole point is to keep the decision in the prompt.
2. **The round budget is the hard guardrail.** Two rounds means investigate-then-answer and nothing else. If you bump `MAX_PLANNING_ROUNDS`, you're inviting investigate→investigate→answer chains that will surprise you.
3. **Investigation results must be visible to the round-2 planner.** If `_summarize_completed_subtasks` is ever changed to filter by `type == "sql"`, investigation breaks silently — round 2 won't know what was discovered.
4. **Investigation results must be hidden from the synthesizer.** The user should not see the discovery queries in the answer text. The synthesizer prompt's existing "no placeholder text" rules don't cover this — the type-skip in `_format_subtask` does.
5. **The 50-row cap is intentional.** Investigation that needs more than 50 rows isn't investigation, it's an answer query in disguise. Don't loosen the cap to "make it work for one question" — make the model write a tighter query.
6. **Investigations count against the 4-subtask cap.** A question that uses 2 investigations leaves room for only 2 answer subtasks. This is fine in practice, and the cap exists to bound latency.
7. **Investigation queries hit the same SQLite-dialect retry hints.** `::cast`, `DATE_TRUNC`, etc. — the small default model (`qwen2.5:3b`) emits Postgres syntax in investigation queries just as readily as in answer queries.
8. **Visualization is correctly skipped for investigation.** The SQL subtask runner adds a visualizer step; the investigate runner does not. If you find yourself copy-pasting the SQL runner and forgetting to drop the visualizer, you'll start producing charts of DISTINCT values.
9. **Strategist must filter investigations out of follow-up suggestions.** Otherwise the user gets "Want to know more about the values 'pending', 'completed', 'cancelled'?" — useless.
10. **Token usage accumulates the failed investigation attempts too.** A turn that retries an investigation once before succeeding bills for both attempts. This is consistent with the SQL retry behavior and the visualization retry behavior — keep it consistent, not free.

---

## 15. Reimplementation Milestones

1. **State**: extend `SubtaskResult.type` literal to include `"investigate"`. No reducer changes.
2. **Planner prompt**: add the fourth subtask type description; add the round-2 lockout line.
3. **Investigate runner**: clone `sql_subtask_runner`, drop visualizer, lower `max_rows` to 50, set `max_retries = 1`.
4. **Workflow wiring**: register the runner and the type→node entry in the subtask map.
5. **SQL generator nudge**: branch on subtask type for the investigation directive in the user message.
6. **Synthesizer skip**: filter out `type == "investigate"` in `_format_subtask`.
7. **Strategist filter**: tighten the SQL-subtask filter to exclude investigation explicitly.
8. **Verify** with a question that needs investigation (`"How many orders are 'fulfilled'?"` against a DB where the column uses `'completed'`) and one that doesn't (`"How many total orders?"`). The first should produce a 2-round trace with one investigation; the second should produce a 1-round trace with zero investigations.
9. **Redeploy** with `docker compose up -d --build --force-recreate backend` (per `CLAUDE.md`) and `docker exec ai-harness-backend-1 grep investigate /app/app/graph/nodes/planner.py` to confirm the new code is live.

---

## 16. Open Questions

- Should investigation be surfaced in a "trace view" UI (planner round, what was investigated, what was found) — or stay completely invisible? Today: invisible.
- Should we support "force-investigate" via a context-config flag for datasets where columns are known to be unguessable (e.g. external systems with cryptic codes)? Or is prompting enough?
- If `MAX_PLANNING_ROUNDS` is bumped to 3, does investigation become a tool the planner overuses? Worth measuring before bumping.
- Should investigation queries get a separate, smaller LLM (e.g. `qwen2.5:3b` even when the answer model is `qwen2.5:7b`)? Discovery queries are simpler and the smaller model may be sufficient — would cut latency.
- Should a successful investigation auto-populate a per-context cache ("we already know `orders.status` ∈ {completed, cancelled, refunded}") that future turns reuse without re-querying? Cross-session caching is in §2 non-goals today, but it's the natural next step if investigation becomes load-bearing.
