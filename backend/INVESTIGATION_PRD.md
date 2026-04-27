# Investigation Subsystem PRD

> Adds a new subtask type — `investigate` — that lets the LLM run small discovery queries against the database before committing to an answering SQL query.

---

## 1. Overview

In a typical LLM-to-SQL pipeline, the planner sees only the static schema (table names, column types, table notes) before generating SQL. When a question hinges on knowing the actual values in a column — enums like `orders.status`, categories like `products.category`, the date range of a fact table, whether a join key has matches — the model has to guess. Wrong guesses produce empty result sets, and a SQL retry loop just guesses differently, often arriving at the same wrong assumption with new syntax.

The investigation subsystem gives the planner a way to spawn small read-only "look at the data" queries (`SELECT DISTINCT`, `COUNT(*)`, `MIN`/`MAX`, `SELECT ... LIMIT 10`), feed the results back into a second planning round, and only then commit to the answering query.

It is **opt-in per question**, decided by the LLM. The application code does not detect "this looks like an enum question" and force-inject a discovery query — that would put reasoning in code rather than in the prompt.

---

## 2. Goals & Non-Goals

**Goals**
- Let the planner decide, per question, whether it needs to peek at data before answering.
- Reuse the existing SQL pipeline (validator, executor, safety) end-to-end — no parallel infrastructure.
- Reuse the existing planner re-plan loop. Round 1 = investigate, round 2 = answer.
- Hide investigation from the user. The synthesizer does not narrate "we ran SELECT DISTINCT…" in the final answer.
- Stay cheap. Investigation queries should be small (DISTINCT/COUNT/sample) and capped tighter than answer queries.

**Non-Goals**
- Investigation for non-SQL subtask types (e.g. docs lookup, lineage). They have their own retrieval; SQL discovery doesn't help them.
- Cross-session caching of investigation results. Per-turn, in-graph state only.
- A separate "investigation budget" config knob. Reuse the existing retry budget and subtask cap.
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
investigate runner
    │   sql generator   (with "this is investigation" nudge)
    │   validator       (same as answer)
    │   executor        (tighter row cap; lower retry budget)
    │
    ▼
subtask join → re-plan
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
sql runner → synthesizer (skips investigate subtasks) → answer
```

The subtask reducer must merge by id so the dual-round subtask list survives re-entry from the planner.

---

## 4. Why Planner-Level, Not Runner-Level

Two designs are worth considering:

1. **Investigation as a planner-spawned subtask** (recommended).
2. **Investigation nested inside the SQL runner** — the SQL runner would, before generating the answer, optionally run a discovery query of its own choosing.

The runner-level design avoids round-budget pressure but splits the decision of *what to look at* across two prompts: the planner picks the question, the runner picks the discovery angle. Planner-level keeps the decision in one place, lets discovery results shape *which* answer subtasks get spawned (maybe none — maybe a docs lookup is more appropriate after seeing the data), and produces a clean, auditable subtask trail. Two rounds is enough in practice (round 1 investigate, round 2 answer), so the budget is non-binding.

**Why not auto-inject discovery before planning?** That hard-codes reasoning into the application: rules like "always SELECT DISTINCT on every TEXT column with low cardinality" bloat every prompt, run queries the LLM may not need, and short-circuit the model's own judgment.

---

## 5. State Schema Changes

Extend the subtask `type` enum to include `"investigate"` alongside the existing `"sql"` (and any other) types.

No new fields. Investigation reuses the existing SQL-shaped fields:
- `generated_sql` — the discovery query.
- `raw_data` — the discovery query's result set.
- `validation_error`, `execution_error`, `attempts` — same retry signal as a normal SQL subtask.

The merge-by-id reducer applies unchanged.

---

## 6. Planner Prompt Changes

### 6.1 System prompt

Introduce the new subtask type. Suggested wording:

> - `investigate` — Use when you need to look at actual data values before writing the answering query. Examples: you don't know what values live in an enum-like column; you need the date range of a table; you want to confirm a join key has matches. Investigation queries must be small: `SELECT DISTINCT col FROM t LIMIT 50`, `SELECT COUNT(*) FROM t`, `SELECT MIN(x), MAX(x) FROM t`, or `SELECT * FROM t LIMIT 10`. Investigation is **never** the final answer.

Reinforce the "prefer 1 subtask" rule by adding: "If you spawn an `investigate` subtask, do not also spawn an `sql` subtask in the same round — wait for the investigation to come back first."

### 6.2 Round-2 prompt enhancement

In round 2 (when prior-round results are summarized for the planner), add a hard line:

> You have already used your investigation budget. You may NOT spawn `investigate` subtasks this round. Either spawn the answering subtask(s) or set `ready_to_answer: true`.

### 6.3 Re-plan summary

The function that renders completed subtask results into the round-2 prompt must include investigation results (columns + first N rows). If it filters by type, broaden it to include investigation — round 2 cannot make use of discovery it can't see.

### 6.4 Round budget

Keep the planning-rounds cap at 2. Round 1 investigates, round 2 answers. Bump higher only if questions start needing investigation followed by multi-subtask answers — measure first.

---

## 7. Investigate Subtask Runner

Add an `investigate` runner modeled on the SQL runner with three differences:

1. **No visualizer step.** Investigation outputs aren't charted.
2. **Tighter row cap.** 50 rows is plenty for DISTINCT / sample queries. Pass the lower cap as an override at the runner level rather than mutating global config.
3. **Lower retry budget.** `max_retries = 1`. Investigations that don't run on the second try aren't worth a third — re-planning is cheaper than thrashing.

The SQL generator, validator, and executor are reused as-is — they don't care about subtask `type` once the prompt nudge is in place.

Wire the runner into the workflow graph: register the node, add it to the type → node map used during fan-out, and connect it to the subtask-join node.

---

## 8. SQL Generator: Type-Aware Prompt Nudge

Branch on the current subtask type in the prompt construction. For `type == "investigate"`, append to the user message:

> This is an **investigation query**, not the final answer. Write ONE small SELECT that surfaces the values, range, or count needed to answer the user's question later. Prefer:
> - `SELECT DISTINCT col FROM t LIMIT 50`
> - `SELECT COUNT(*) FROM t WHERE ...`
> - `SELECT MIN(col), MAX(col) FROM t`
> - `SELECT * FROM t LIMIT 10`
>
> Do NOT attempt to answer the user's question here. Do NOT compute aggregations beyond simple COUNT/MIN/MAX. The result will inform a follow-up query.

Keep all existing dialect retry hints. Investigation queries hit the same dialect quirks as answer queries.

---

## 9. Synthesizer: Hide Investigation From Final Answer

In whatever function renders subtask results into the final answer, skip subtasks where `type == "investigate"`. The synthesizer renders only user-facing types. Investigation is scaffolding — the user should never see "we first checked DISTINCT statuses" in the answer.

If every successful subtask in the final state is an investigation (i.e. round 2 produced no answer), the synthesizer's existing empty-results path should trigger cleanly.

---

## 10. Strategist / Follow-Up Suggester

Anything that suggests follow-up questions based on prior results must filter out investigation subtasks. An investigation like "DISTINCT status values" is not a useful seed for "what other questions might you ask?"

---

## 11. Safety, ACL, and Execution Guardrails

Investigation queries flow through **the same** safety validator and table-ACL check as answer queries:

- **SQL safety filter** — blocks INSERT/UPDATE/DELETE/DROP/etc. Applies to investigation unchanged.
- **Table ACL** — investigation queries must reference tables in the user's visible-tables context, with the same CTE-name exclusion logic.
- **Read-only engine flag** (e.g. `PRAGMA query_only = ON` for SQLite, read-only role for Postgres) — applies to all queries.
- **Timeout** — same 30s (or whatever the answer-query timeout is).
- **Row cap** — the single deliberate divergence: ~50 rows for investigation vs. the larger answer-query cap. Implement via a parameter override at the runner, not a global config change.

Skipping the validator/ACL on investigation queries because "it's just discovery" reintroduces a footgun. Investigation queries are still SQL the LLM wrote against a user-scoped context — they get the same checks.

---

## 12. Token Accounting

Investigation adds at minimum one extra LLM call per question that uses it (planner round 2 + sql generator for the investigation). Token-usage state should accumulate via a list-append reducer so parallel/sequential subtask runs concatenate cleanly. Confirm the new runner aggregates token usage from each step (generator → validator → executor) the same way the SQL runner does.

A turn that uses investigation pays for: planner round 1 + investigate generator + planner round 2 + sql generator + (validator/executor are not LLM calls) + synthesizer + follow-up suggester. Roughly 1.5–2× a non-investigating turn.

---

## 13. Failure Modes

| # | Failure | Behavior |
|---|---|---|
| 1 | Planner spawns an investigation in round 1 that fails validation/execution after 1 retry | Subtask marked completed with errors. Round 2 planner sees the failure in the summary and either commits to a best-guess SQL or falls through to the synthesizer's empty-results path. |
| 2 | Investigation succeeds but returns 0 rows (e.g. `SELECT DISTINCT status FROM orders` on an empty table) | Empty result set is rendered to round-2 planner. Planner should infer "no data" and answer accordingly. |
| 3 | Round-1 planner spawns both `investigate` and `sql` simultaneously | Prompt rule says don't. If the LLM violates it anyway, both run in parallel — the SQL answer query proceeds without seeing the investigation. Acceptable degradation; do not enforce in code. |
| 4 | Round-2 planner tries to spawn another `investigate` despite the prompt rule | Either (a) allow it through and log a warning, or (b) coerce it to a regular SQL subtask in the planner adapter so the user still gets an answer. Small models drift on this rule — the coercion backstop is recommended. |
| 5 | All subtasks in final state are `investigate` (synthesis has nothing to render) | Synthesizer's existing empty-state path handles it. The user gets "I couldn't find data to answer that" rather than a chart of DISTINCT values. |
| 6 | Investigation query times out | Same as any SQL timeout — caught by executor, surfaces as `execution_error`, retry-once-then-give-up. |

---

## 14. Non-Obvious Behaviors

These are the things that bite re-implementers.

1. **Investigation is opt-in, decided by the LLM.** Do not add code-side heuristics that auto-spawn investigation for "enum-shaped" columns. The whole point is to keep the decision in the prompt.
2. **The round budget is the hard guardrail.** Two rounds means investigate-then-answer and nothing else. Bumping the round cap invites investigate→investigate→answer chains that will surprise you.
3. **Investigation results must be visible to the round-2 planner.** If the re-plan summary is ever changed to filter by `type == "sql"`, investigation breaks silently — round 2 won't know what was discovered.
4. **Investigation results must be hidden from the synthesizer.** The user should not see the discovery queries in the answer text. A type-skip in the result-formatting function is the cleanest place to enforce this.
5. **The 50-row cap is intentional.** Investigation that needs more than 50 rows isn't investigation, it's an answer query in disguise. Don't loosen the cap to "make it work for one question" — make the model write a tighter query.
6. **Investigations count against the overall subtask cap.** A question that uses 2 investigations leaves room for fewer answer subtasks. This is fine in practice and bounds latency.
7. **Investigation queries hit the same SQL-dialect retry hints as answer queries.** Small models will emit Postgres syntax in investigation queries just as readily as in answer queries.
8. **Visualization is correctly skipped for investigation.** If you copy-paste the SQL runner and forget to drop the visualizer, you'll start producing charts of DISTINCT values.
9. **Follow-up suggestion must filter investigations out.** Otherwise the user gets "Want to know more about the values 'pending', 'completed', 'cancelled'?" — useless.
10. **Token usage accumulates failed investigation attempts too.** A turn that retries an investigation once before succeeding bills for both attempts. Keep this consistent with the SQL retry behavior.
11. **Hide scaffolding types in three places.** Whenever you add an internal-only subtask type, filter it out of (a) the synthesizer's rendering, (b) the follow-up suggester's input, and (c) any next-turn session-history artifact builder. Missing any one leaks scaffolding into either the answer, the suggestions, or the next turn's context.

---

## 15. Implementation Milestones

1. **State**: extend the subtask `type` enum to include `"investigate"`. No reducer changes.
2. **Planner prompt**: add the new subtask type description; add the round-2 lockout line.
3. **Investigate runner**: clone the SQL runner, drop visualizer, lower row cap to ~50, set `max_retries = 1`.
4. **Workflow wiring**: register the runner and the type→node entry in the subtask map.
5. **SQL generator nudge**: branch on subtask type for the investigation directive in the user message.
6. **Synthesizer skip**: filter out `type == "investigate"` in the result-rendering function.
7. **Follow-up suggester filter**: tighten the SQL-result filter to exclude investigation explicitly.
8. **Round-2 coercion backstop** (optional): downgrade any round-2 `investigate` subtask to `sql` in the planner adapter, with a warning log.
9. **Verify** with a question that needs investigation (e.g. `"How many orders are 'fulfilled'?"` against a DB where the column uses `'completed'`) and one that doesn't (`"How many total orders?"`). The first should produce a 2-round trace with one investigation; the second should produce a 1-round trace with zero investigations.

---

## 16. Open Questions

- Should investigation be surfaced in a "trace view" UI (planner round, what was investigated, what was found) — or stay completely invisible?
- Should there be a "force-investigate" config flag for datasets where columns are known to be unguessable (external systems with cryptic codes)? Or is prompting enough?
- If the round cap is bumped beyond 2, does investigation become a tool the planner overuses? Worth measuring before bumping.
- Should investigation queries get a separate, smaller LLM than the answering query? Discovery queries are simpler; a smaller model may suffice and would cut latency.
- Should a successful investigation auto-populate a per-context cache ("we already know `orders.status` ∈ {completed, cancelled, refunded}") that future turns reuse without re-querying? Cross-session caching is out of scope today, but it's the natural next step if investigation becomes load-bearing.
