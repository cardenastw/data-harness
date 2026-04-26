# Multi-Subtask Planner-Executor for Chat Workflow

## Context

Today, one user message routes to exactly one branch (sql OR docs OR lineage) and produces one artifact: one SQL query, or one doc lookup, or one lineage record. Questions like *"What was revenue last month and what is our definition of net revenue?"* or *"Show me revenue trend and top customers"* either fail or get crammed into a single SQL query that loses fidelity.

**Goal**: a single user message can trigger multiple SQL queries plus optional doc/lineage lookups, all returned in one assistant response. Adaptive re-planning is allowed — the planner can fire a second round of subtasks after seeing initial results before composing the final answer.

**Non-goal**: streaming. Backend stays single-shot JSON per CLAUDE.md.

The frontend already renders `artifacts: Artifact[]` as an array (`frontend/src/components/MessageBubble.tsx:19-40`), so the bottleneck is the backend producing only one artifact per type. The state has singleton fields that must become lists, and the router that picks one branch must become a planner that picks N subtasks.

---

## Architecture

Replace the single-shot router with a **planner-executor loop**:

```
context_gatherer
   ↓
planner ────────────────┐  (round 1 + optional round 2)
   ↓                    │
fan-out via Send        │
to subtask executors    │
   ↓                    │
[parallel subgraphs]    │
   ↓                    │
join (reducer-merged)   │
   ↓                    │
ready_to_answer? ───────┘  (no → planner with completed results)
   ↓ yes
synthesizer + strategist (parallel)
   ↓
END
```

Each subtask routes to a **single runner node** (`sql_subtask_runner`, `docs_subtask_runner`, `lineage_subtask_runner`) that procedurally invokes the existing inner step functions (`sql_generator → validator → executor → visualization` for sql, `docs_lookup → docs_answer` for docs, `lineage_lookup → lineage_answer` for lineage). Originally these were going to be wired as separate LangGraph nodes, but `Send` only propagates the `_current_subtask` payload to its immediate destination — subsequent nodes via `add_edge` don't see it (see *Implementation notes* below). The existing inner nodes still exist as standalone callables and are reused inside the runners.

**Bounds (safety)**:
- Max 2 planning rounds per turn
- Max 4 total subtasks across rounds
- Max 3 retries per SQL subtask (unchanged)
- Hard ceiling: 15 LLM calls per turn (planner + per-subtask LLMs + synthesizer)

---

## State changes — `backend/app/graph/state.py`

Replace the singleton SQL/docs/lineage fields with a per-subtask shape collected via reducer.

```python
class SubtaskResult(TypedDict, total=False):
    subtask_id: str           # "s1", "s2", ...
    type: Literal["sql", "docs", "lineage"]
    question: str             # planner's per-subtask question
    reason: str               # planner's rationale (used by synthesizer)

    # SQL subtask fields
    generated_sql: str
    raw_data: Optional[dict]
    chart_json: Optional[dict]
    validation_error: Optional[str]
    execution_error: Optional[str]
    sql_attempts: int

    # Docs subtask fields
    docs_results: Optional[list[dict]]
    docs_answer_text: Optional[str]

    # Lineage subtask fields
    lineage_node: Optional[dict]
    lineage_known: Optional[dict]
    lineage_answer_text: Optional[str]

    # Status
    error: Optional[str]
    completed: bool

class GraphState(TypedDict, total=False):
    # Input + context (unchanged)
    user_question: str
    context_id: str
    session_messages: list[dict]
    system_prompt: str
    schema_text: str
    context_config: Any

    # NEW: planner output and accumulated results
    subtasks: Annotated[list[SubtaskResult], merge_subtasks_by_id]
    planning_rounds: int       # incremented by planner
    ready_to_answer: bool      # planner's verdict; drives the loop edge

    # Synthesizer output (replaces former answer_text role)
    answer_text: Optional[str]
    suggestions: list[str]

    # Reducer-merged usage (already exists)
    token_usage: Annotated[list, add]
    error: Optional[str]
```

**Reducer**: `merge_subtasks_by_id(left, right)` — entries with matching `subtask_id` overwrite (last-write-wins); new ids append. This is critical because SQL self-correction re-enters the same subtask multiple times and we want the latest result, not duplicate appends.

Remove from top level: `generated_sql`, `raw_data`, `chart_json`, `validation_error`, `execution_error`, `sql_attempts`, `question_type`, `routing_subject`, `docs_results`, `lineage_node`, `lineage_known`. They all live inside `SubtaskResult` now.

---

## Workflow rewiring — `backend/app/graph/workflow.py`

1. **Replace** `router_node` registration with `planner_node`.
2. **Add** `synthesizer_node` and three runner registrations (`sql_subtask_runner`, `docs_subtask_runner`, `lineage_subtask_runner`).
3. After `planner`, a conditional edge fans out via `Send` — one Send per pending subtask, targeted at its type's runner. Each Send carries `{**state, "_current_subtask": st}` so the runner sees the full state plus its scoped subtask.
4. All three runners → `subtask_join` (a no-op convergence node).
5. From `subtask_join`, conditional edge: re-plan (`planner`) if `ready_to_answer == false` and `planning_rounds < 2`, else fan out to `[synthesizer, strategist]` in parallel.
6. `synthesizer → END`, `strategist → END`.
7. The runner consolidation is critical: the original plan to wire `sql_generator → validator → executor → visualization` as separate LangGraph nodes inside the subtask path *does not work* — `_current_subtask` is dropped after the first node. Each runner does the chain procedurally inside one node so the scope persists. See *Implementation notes & gotchas* below.

`max_sql_retries` is passed to the SQL runner via closure at workflow build time, NOT via state. (LangGraph strips state fields not declared on `GraphState`, so `state["_max_retries"]` doesn't survive the round-trip — burned an iteration discovering this.) `WorkflowRunner.ainvoke` no longer injects retry config into state.

---

## New node — `backend/app/graph/nodes/planner.py`

Replaces `router.py`. Same JSON-extraction scaffolding (lift `_parse_routing` from router and generalize to `_parse_plan`).

**System prompt shape** (concrete; do not reproduce in code, just use as guidance for the prompt file):

```
You are a planner for a data analyst assistant. Read the user's question and the
results of any subtasks already completed, and decide what to do next.

You can call three kinds of tools:
- "sql": numeric data, aggregates, top-N, trends, breakdowns.
- "docs": definitions, business rules, policies, glossary.
- "lineage": where a metric/column/table comes from.

Return ONLY a JSON object:
{
  "reasoning": "<one sentence on what the user is asking and your plan>",
  "ready_to_answer": <true | false>,
  "new_subtasks": [
    {"id": "s1", "type": "sql", "question": "...", "reason": "..."},
    ...
  ]
}

Rules:
- Prefer ONE subtask when one query/lookup can answer the question.
- Only split into multiple SQL subtasks when the questions are about different
  metrics, time ranges, or grain — NOT when one query can produce both columns.
- Combine SQL and docs in one plan when the user asks both for a number AND its
  definition.
- If the prior round's results already answer the question, set
  ready_to_answer=true and new_subtasks=[].
- Cap: at most 4 total subtasks across all rounds.

[Negative examples to discourage over-decomposition]
[Positive examples for genuine multi-subtask cases]
```

The prompt receives the user question, prior session messages, and (on round 2) a compact summary of completed subtasks: `[s1: sql, "revenue last month", → 1 row, $X] [s2: docs, "net revenue", → matched 2 docs]`.

**Output handling**:
- Increment `planning_rounds`.
- Append `new_subtasks` to state with fresh ids.
- Set `ready_to_answer` from LLM output.
- Hard cap: if total subtasks > 4 or `planning_rounds >= 2`, force `ready_to_answer=true` and drop overflow.

Per CLAUDE.md ("LLM owns reasoning, not Python"): the only Python-side intervention is the safety cap. Decomposition logic is entirely in the prompt.

---

## New node — `backend/app/graph/nodes/synthesizer.py`

Composes the single user-facing assistant text from all subtask results.

**System prompt shape**:

```
You are writing the final answer to the user's question, given the results of
the subtasks below. Write a single coherent natural-language answer.

- Cite specific numbers and findings.
- If a subtask failed, state that plainly and skip it. Do NOT invent results.
- Do not show SQL or technical details — those render separately.
- Reference docs by title when used.
- Keep it concise (2-4 sentences for simple cases, 1-2 short paragraphs for
  complex ones).
```

Receives `user_question` + a formatted bundle:
- For each SQL subtask: `question, reason, generated_sql (1-line), row_count, columns, first 3 rows, error?`
- For each docs subtask: `question, reason, doc titles + snippets, docs_answer_text, error?`
- For each lineage subtask: `question, reason, lineage_node summary, lineage_answer_text, error?`

Writes `answer_text` to top-level state.

Per CLAUDE.md ("don't short-circuit the LLM loop"): synthesizer is mandatory. Do not concatenate Python-side.

---

## Existing-node changes

The existing nodes are NOT wired as direct LangGraph nodes inside subtask paths anymore — they're called as plain async functions from inside the three runner nodes (`sql_subtask_runner_node` / `docs_subtask_runner_node` / `lineage_subtask_runner_node` in `backend/app/graph/nodes/subtask_runners.py`). Each inner node still reads from `state["_current_subtask"]` and returns updates shaped as:

```python
return {"subtasks": [{"subtask_id": current.id, **updates}]}
```

The reducer merges by id. The runner re-binds `_current_subtask` and `subtasks` on a local "scoped" state on each call so the inner node sees the latest values. The runner additionally needs to read the freshest field values (e.g. `validation_error` written by `validator` becomes visible to `sql_generator` on the next retry) by walking the merged subtasks list — see `_apply_subtask_updates` in `subtask_runners.py`.

| File | Change |
|---|---|
| `backend/app/graph/nodes/sql_generator.py` | Read `user_question` from current subtask, not top-level. Read `validation_error`/`execution_error`/`generated_sql`/`sql_attempts` from current subtask. Continue including failed SQL alongside the error per CLAUDE.md self-correction rule. Write back into the subtask. |
| `backend/app/graph/nodes/validator.py` | Read `generated_sql` from current subtask, write `validation_error` into it. |
| `backend/app/graph/nodes/executor.py` | Read `generated_sql` from current subtask, write `raw_data`/`execution_error` into it. |
| `backend/app/graph/nodes/visualization.py` | Read `raw_data`/`generated_sql`/`user_question` from current subtask. Write `chart_json` into the subtask. Set `completed=True`. |
| `backend/app/graph/nodes/docs_lookup.py` | Read `question` from current subtask. Write `docs_results` into it. |
| `backend/app/graph/nodes/docs_answer.py` | Read `docs_results`/`question` from current subtask. Write `docs_answer_text` into it. Set `completed=True`. |
| `backend/app/graph/nodes/lineage_lookup.py` | Read `question` from current subtask. Write `lineage_node`/`lineage_known` into it. |
| `backend/app/graph/nodes/lineage_answer.py` | Read `lineage_node`/`lineage_known`/`question` from current subtask. Write `lineage_answer_text` into it. Set `completed=True`. |
| `backend/app/graph/nodes/strategist.py` | Read all SQL subtasks from `state["subtasks"]`, generate cross-cutting follow-ups. Single invocation at the end. |
| `backend/app/graph/nodes/context_gatherer.py` | Initialize `planning_rounds=0`, `subtasks=[]`. Otherwise unchanged. |
| `backend/app/graph/nodes/router.py` | **Delete** (replaced by `planner.py`). Lift its `_parse_routing` and `_extract_usage` helpers into `planner.py`. |

The `_route_after_validation` and `_route_after_execution` helpers in `workflow.py` become subtask-scoped (read attempts from `_current_subtask` not top-level state).

---

## API + frontend changes

### Backend response — `backend/app/api/schemas.py`

Add a structured artifact list. Drop the singletons (single-ship per user's choice):

```python
class ChatResponse(BaseModel):
    session_id: str
    answer_text: Optional[str] = None       # synthesizer output (was: docs/lineage answer)
    artifacts: List[Dict[str, Any]] = []    # NEW: list of subtask artifacts
    suggestions: List[str] = []
    usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
```

Each artifact dict:
```python
{
  "type": "sql" | "docs" | "lineage",
  "subtask_id": "s1",
  "question": "...",
  "reason": "...",
  # type-specific fields:
  "sql": "...", "raw_data": {...}, "chart_json": {...},  # for sql
  "docs": [...],                                          # for docs
  "lineage": {...},                                       # for lineage
  "error": "..." | None,
}
```

### Chat route — `backend/app/api/routes/chat.py`

Replace lines 38–95 to:
1. Read `result["subtasks"]` and `result["answer_text"]`.
2. Build `artifacts` list from each subtask, omitting incomplete ones.
3. Persist a richer assistant message in `session.messages`:
   ```python
   {"role": "assistant", "content": answer_text, "artifacts_summary": [...]}
   ```
   Where `artifacts_summary` is a compact text-friendly summary (sql + row_count + first 2 rows; doc titles; lineage name) — NOT full row data, to avoid session bloat.
4. When building `session_messages` for the *next* turn (line 32), flatten the artifacts_summary into the `content` string the LLM sees, so the planner has context about prior queries: `"<answer_text>\n[Prior queries: SELECT ... → 12 rows; matched docs: ...]"`. Otherwise the planner loses memory of what was already fetched.

### Frontend — `frontend/src/api/client.ts`

Replace lines 71–131:
- Read `data.artifacts` directly (it's already the array shape).
- Map each artifact to the existing `Artifact` type.
- Keep the `data.error` early throw.
- Drop the singleton-to-array translation block.

### Frontend types — `frontend/src/types/index.ts`

Extend `Artifact` with optional `subtaskId?: string`, `question?: string`, `reason?: string` so the UI can label which artifact answered which sub-question.

### Frontend rendering — `frontend/src/components/MessageBubble.tsx`

Render `artifact.question` as a small subheading above each artifact, when present. Cosmetic only — no logic change.

---

## Critical files

- `backend/app/graph/state.py` — state migration (singletons → list with `merge_subtasks_by_id` reducer)
- `backend/app/graph/workflow.py` — wire planner, fan-out via `Send`, re-plan loop, synthesizer, strategist
- `backend/app/graph/nodes/planner.py` (new, replaces `router.py`) — multi-subtask planner with re-plan capability and prior-turn session-history injection
- `backend/app/graph/nodes/synthesizer.py` (new) — composes final answer from all subtask results
- `backend/app/graph/nodes/subtask_runners.py` (new — NOT in original plan) — three runner nodes that procedurally invoke the inner step functions; required because `Send` only injects state into its first destination
- `backend/app/graph/nodes/{sql_generator,validator,executor,visualization,docs_lookup,docs_answer,lineage_lookup,lineage_answer,strategist,context_gatherer}.py` — read/write per-subtask scope
- `backend/app/api/schemas.py` — `ChatResponse.artifacts: List`
- `backend/app/api/routes/chat.py` — assemble `artifacts` list, flatten artifact summaries into session content for next-turn planner context
- `frontend/src/api/client.ts` — read `artifacts` directly; map api-shape `{sql, raw_data, chart_json}` → typed `{query, result, chart}`
- `frontend/src/types/index.ts` — extend `Artifact` with `subtaskId/question/reason/answerText/error`. Dropped the separate `"chart"` artifact type — chart is now a property of the SQL artifact
- `frontend/src/components/MessageBubble.tsx` — render per-artifact question label when there's >1 artifact (single-subtask UX is visually identical to before)
- `frontend/src/App.css` — styles for `.artifact-header`, `.artifact-type-tag`, `.artifact-question`, `.artifact-error`

---

## Risks & mitigations

1. **Planner over-decomposition** (splits "revenue and orders" into 2 queries when 1 column-pair query works). Mitigation: explicit prompt rule "prefer ONE SQL subtask when one query produces multiple columns" + 3+ negative examples + telemetry on subtask count.
2. **Latency** — extra LLM round-trip for planning (and possibly a second). Subtasks fan out in parallel via `Send`, so multi-subtask cost is `~max(t_subtask)` not sum. Worst case: 2 plan rounds + 4 subtasks = 6+ LLM calls per turn, cap at 15.
3. **Session bloat** — full `raw_data` in session would explode. Persist summaries only (row count + first 2 rows), full data lives in client memory.
4. **Prior-turn memory loss** — if assistant content is just `answer_text`, the planner forgets what was already fetched. Mitigation: flatten artifact summaries into history content (item 4 in chat route changes).
5. **Synthesizer hallucination** — could invent unfetched data. Mitigation: prompt rule "do not invent results; if a subtask failed, say so plainly". Pattern matches existing `docs_answer_node`.
6. **Re-plan loop runaway** — guard with `max_planning_rounds=2` enforced in Python (state cap, not just prompt).
7. **Per-turn LLM call budget runaway** — `4 subtasks × 3 retries + 2 plans + synthesizer = 15`. Add a hard ceiling check in `_route_after_validation`/`_route_after_execution`: if global budget exhausted, abort and let synthesizer summarize what we have.
8. **Frontend chart clutter** — 4 SQL subtasks → 4 charts. Acceptable as v1; iterate UX later. The existing artifact-array renderer already handles it.
9. **Reducer correctness** — `merge_subtasks_by_id` must be commutative-enough for parallel `Send` writes. Strategy: writes from different subtasks don't conflict (different ids); writes within the same subtask (retry loop) are sequential within that subtask's edges, so last-write-wins is safe. Add a unit test on the reducer.

---

## Implementation notes & gotchas

These are things the original plan got wrong, or didn't anticipate. Future contributors should read this section before changing anything load-bearing.

### LangGraph `Send` does NOT carry payload state past the first destination

The original plan assumed each subtask path could be wired as a chain of LangGraph nodes (`sql_generator → validator → executor → visualization`) and that `Send`'s `_current_subtask` payload would propagate through the chain. **It does not.** `Send` injects state only into the immediately-destined node. After that node returns, subsequent nodes via `add_edge` read from the global channels — fields not declared in the state schema (or fields not returned by the previous node) are gone.

Confirmed empirically: `_current_subtask=s1` was visible in `sql_generator` but `None` in `validator`. Writing `_current_subtask` back from each node would race between parallel branches (different subtasks would clobber each other in the global channel). **Solution**: collapse each subtask's pipeline into a single runner node (`subtask_runners.py`) that calls the inner steps procedurally inside one LangGraph invocation, where the local Python `current` dict naturally persists.

### LangGraph strips state fields not declared on `GraphState`

`WorkflowRunner.ainvoke` originally tried `state["_max_retries"] = self._max_retries` to thread retry config through to the runner. That field does not appear in `GraphState` (and shouldn't — it's config, not state), so LangGraph drops it before the first node runs. The runner saw `MISSING` and used the hard-coded default of 3, which was wrong.

**Solution**: pass config via closure at workflow build time. `sql_subtask_runner_node(..., max_retries=deps.max_sql_retries)` captures the value in the closure and the inner `_run` reads it from there, not from state. Generalizes: anything that doesn't change per-invocation should be a closure capture, not a state field.

### Validator regex flagged CTE names as unauthorized tables

The plan didn't mention this. The existing table-access check uses `\b(?:FROM|JOIN)\s+(\w+)` — a naive regex that matches `FROM cte_name` references inside the main query, not just table references. Once the LLM started writing CTE-heavy SQL (which is exactly what we want for net_revenue's `UNION ALL` over orders + cart_orders), every CTE got rejected. The model would then "fix" it by inventing different fake names, looping until retries exhausted.

**Solution**: added a `_CTE_NAME_PATTERN = r"\b(\w+)\s+AS\s*\(\s*(?:SELECT|WITH|VALUES)\b"` extractor. The keyword inside the parens is the discriminator that prevents column aliases (`SUM(x) AS total`) and subquery aliases (`(SELECT ...) AS sub`) from being misidentified as CTEs. The check now subtracts `cte_names` from `referenced` before comparing against `visible_tables`. Same fix applied to `visualization.py`'s chart SQL validation. Also lowercased everything so `Cart_Orders` vs `cart_orders` doesn't trip it.

### Validator error must echo the allowed table list, not just the unauthorized names

When validation rejects a query with "unauthorized tables: {x, y}", the LLM has no idea what IS allowed and just hallucinates a different fake name on retry. **Solution**: include the full sorted allowed-tables list in the error text plus an explicit hint to use CTEs/subqueries for derived datasets. Dramatically reduced the loop-to-failure rate.

### Synthesizer latches onto bracketed labels and emits placeholder strings

When the per-subtask format used `[s1] sql — Net revenue last month`, small models would echo the bracket convention back as `[amount from s1 query]` literal text in the answer. The `do not invent results` instruction wasn't enough; the model treated `[...]` as fillable templates.

**Solution**: replaced bracket labels with natural prose (`Subtask 1 (sql) — asked: 'Net revenue last month'`), added an explicit `STATUS: OK` / `STATUS: FAILED` line, and added a `Do NOT fabricate a number for this subtask` instruction inline with each failed subtask. Also added "CRITICAL RULES" to the synthesizer system prompt explicitly forbidding placeholders like `[X]`, `[query result]`, `(see result above)`.

### Planner needs prior-turn `session_messages`

Originally the planner only saw `{system, user_question}`. On a follow-up turn it had no idea what the previous turn fetched and would re-plan the same subtasks (e.g. user asks "and last week?" → planner has no idea what "this week" returned). **Solution**: thread `session_messages` into the planner's LLM call (system → ...prior turns... → current user question). Combined with chat.py flattening artifact summaries into the assistant's persisted content, the planner now sees `[Prior subtasks this turn: [s1] sql: 'q' → SELECT ... → 12 rows; columns=[...]]` in the conversation history.

### Frontend chart artifact shape changed

The original frontend treated `chart` as a separate `Artifact` with `type: "chart"`. The backend used to emit two singletons (`sql` + `chart_json`) and the client would push two artifacts. With the new per-subtask shape, chart belongs to its SQL subtask. **Solution**: dropped the `"chart"` Artifact type entirely; `Artifact` of `type: "sql"` now optionally carries a `chart` property. `MessageBubble` renders SqlBlock + DataTable + ChartRenderer in one artifact div.

### Small models leak Postgres syntax in many shapes

`qwen2.5:3b` (the default model) repeatedly produced PostgreSQL-only constructs even with explicit "this is SQLite, not PostgreSQL" in the system prompt:

- `value::date` typecasts
- `DATE_TRUNC`, `EXTRACT`, `NOW()`, `INTERVAL`
- `:name` parameter placeholders
- `E'...'` and `$$...$$` quoting
- `column = ANY (subquery)` / `= ALL (subquery)`
- `CONCAT(...)` instead of `||`
- `true` / `false` literals instead of `1` / `0`

Each variant had to be added to a targeted retry hint in `sql_generator.py` that fires when the executor error contains `unrecognized token`, `no such function`, or `syntax error`. The base system prompt warns about these but small models don't carry the warnings forward across attempts — putting the reminder right next to the failed SQL in the retry message gives a much stronger signal.

Same pattern: a UNION-mismatch hint fires when the error contains `same number of result columns` (LLM's frequent mistake of `SELECT * FROM orders UNION ALL SELECT * FROM cart_orders` when the schemas differ), and a no-such-column hint when CTE columns aren't projected through.

This is whack-a-mole territory. **Better mitigation**: use a stronger model. `qwen2.5:7b` is ~10× better at following SQL-dialect rules and rarely needs more than one retry on these kinds of queries.

### Visualization marks `completed=True` even when SQL failed

By design — the SQL runner always invokes `visualization` after the retry loop terminates, regardless of success. If `raw_data` is missing, visualization returns `{chart_json: None, completed: True}` without calling the LLM. This is how a fully-failed SQL subtask still terminates cleanly and reaches `subtask_join`. Without this, the join would hang waiting for a path that never marks complete.

### Docker `compose up` does not reload Python sources

The `up` command without `--build` reuses the existing image. Even `up --build` may keep the existing container alive if nothing else changed. **Reliable incantation after a backend code edit:**

```bash
docker compose up -d --build --force-recreate backend
```

`--force-recreate` ensures the new image actually replaces the live container. Verify the new code is in the running container with `docker exec ai-harness-backend-1 grep <signature> /app/...` before debugging behavior.

### Reducer must merge by id, not append

`Annotated[list, operator.add]` would append on every write — the SQL retry loop re-enters the same subtask 4 times and would create 4 entries with the same `subtask_id`. Custom reducer `merge_subtasks_by_id` does last-write-wins per id with shallow field merging, preserves insertion order on first appearance, and handles `None`/empty inputs.

---


   - `merge_subtasks_by_id` reducer: append for new ids, overwrite for existing, parallel-write order independence within different ids.
   - Planner JSON parser: handles code fences, extra prose, malformed output (falls back to single SQL subtask like router does today).
   - Cap enforcement: ≤ 4 subtasks even if planner emits 10; `planning_rounds` capped at 2.

2. **Integration tests** (happy paths):
   - Single SQL question → 1 subtask, behaves identically to today.
   - "Revenue last month and what does net revenue mean" → 1 SQL + 1 docs subtask in parallel; synthesizer cites both.
   - "Show revenue trend AND top customers" → 2 SQL subtasks, 2 charts in artifacts.
   - Re-plan: question whose answer needs a follow-up (e.g. "biggest dropoff month, then drill into it") triggers 2 planning rounds.

3. **Integration tests** (failure paths):
   - One subtask SQL execution fails: synthesizer reports the failure, other subtasks still rendered.
   - Planner emits malformed JSON: falls back to single SQL subtask (no regression).
   - Hits LLM call budget: graceful synthesis from partial results.

4. **End-to-end manual**:
   - `docker compose up`, hit the frontend.
   - Verify multi-subtask question renders multiple artifacts in one assistant bubble with correct labels.
   - Verify follow-up turn sees prior subtask context (planner doesn't re-query the same data).
   - Confirm token usage in `/api/chat` response sums correctly across planner + subtasks + synthesizer.

5. **Telemetry to add post-merge**: subtask count per turn, planning rounds per turn, retries per subtask. Use these to tune the planner prompt.

---

## Status (as of implementation merge)

**Shipped and verified**:
- All architecture pieces: planner, three subtask runners, synthesizer, strategist, subtask_join, conditional re-plan loop
- State migration: singletons → reducer-merged subtasks list
- Frontend: artifact-array rendering, per-artifact question header when N>1
- Self-correction loops with progressively richer retry hints (CTE-aware validator, allowed-table list, SQLite-vs-Postgres reminders, UNION column-count, no-such-column)
- Multi-turn session continuity (planner sees prior-turn artifact summaries)
- Round cap, subtask cap, retry cap all enforced

**Tested via FastAPI TestClient + stubbed deps**:
- Single SQL question (1 subtask, no behavior regression)
- SQL + docs in parallel (synthesizer cites both)
- SQL retry on execution error (failed SQL included in retry context per CLAUDE.md)
- Adaptive re-plan round 2 (planner sees prior subtasks)
- Round cap (LLM keeps saying not-ready, system caps at 2)
- Error paths (missing context_id, bad session_id, unknown context)
- Multi-turn (turn 2 planner sees turn 1's artifact summary in history)

**Known operational issue**: small models (`qwen2.5:3b`) emit Postgres-isms and column-mismatched UNIONs on hard finance queries. Each new failure shape gets a targeted retry hint, but the better fix is a larger model. Architecture handles failures gracefully — the subtask completes with `error` set, the synthesizer reports it cleanly, the docs subtask answer is still useful.

**Not built (deferred)**:
- Phase 3 from the original plan: subtask dependencies (`depends_on: [id]`)
- Unit tests in a `tests/` directory (verified via in-line scratch tests during implementation, but no committed test files)
- Per-subtask LLM-call budget ceiling (only the per-turn subtask cap and per-subtask retry cap are enforced)
- Telemetry counters

