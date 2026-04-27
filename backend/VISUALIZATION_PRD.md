# Visualization Subsystem PRD

> Companion to `backend/PRD.md`. This document specifies the visualization path in enough detail that someone can re-implement it from scratch in a different stack without inheriting the surprises.

---

## 1. Overview

After a successful analytical query, the visualization subsystem produces a Recharts-compatible chart spec to render alongside the raw answer. It does this by making a **second LLM call** that writes a **separate SQL query** specifically shaped for charting, executes that query, and shapes the result into a frontend-ready JSON payload.

It is not a "given some rows, draw a chart" function. It is its own LLM agent with its own self-correcting retry loop and its own database round trip.

---

## 2. Goals & Non-Goals

**Goals**
- Always attempt a chart — even when the user asked for a single number.
- Self-correct: if the chart query is invalid, fails to execute, or produces unchartable data, feed the failure back to the LLM and try again, up to a hard cap.
- Run in parallel with sibling nodes (e.g. follow-up suggestions) so the user-perceived latency is `max(chart, suggestions)`, not `chart + suggestions`.
- Fail silently. A failed chart must not break the answer.
- Stay data-source-agnostic by keeping all dialect-specific SQL hints in one promptable seam (see §13).

**Non-Goals**
- Streaming the chart spec.
- Caching chart specs across turns.
- Letting the LLM choose arbitrary chart libraries — output is a fixed Recharts-shaped JSON contract.
- Server-side rendering of the chart image.

---

## 3. End-to-End Flow

```
raw_data present  ──▶  visualization_node
                          │
                          ▼
                 ┌────────────────────┐
                 │ ask LLM for spec   │  ◀── on retry: include failed SQL + error
                 │ {query,            │
                 │  chart_type,       │
                 │  title}            │
                 └─────────┬──────────┘
                           │
                           ▼
                 parse JSON ──── unparseable ──▶ retry
                           │
                           ▼
                 safety validator ──── unsafe ──▶ retry (with reason)
                           │
                           ▼
                 table-ACL check ──── unauthorized ──▶ retry (with table list)
                           │
                           ▼
                 execute SQL (2nd DB round trip) ──── error / timeout ──▶ retry
                           │
                           ▼
                 shape rows into chart JSON ──── unchartable ──▶ retry
                           │
                           ▼
                       chart_json
```

After `MAX_CHART_ATTEMPTS` (3), the node returns `chart_json: null` with all accumulated token usage. No error surfaces in the API response.

---

## 4. The "Two Queries" Principle

**This is the single most important thing to preserve.** The chart query is **not** the answer query.

- The answer query may be `SELECT SUM(revenue) FROM orders WHERE month = '2026-03'` — a single scalar.
- The chart query for the same turn might be `SELECT date(order_date) AS day, SUM(revenue) AS revenue FROM orders WHERE month = '2026-03' GROUP BY day ORDER BY day` — a daily breakdown of that same scalar.

The LLM is given the user question, the answer SQL, and the schema, and is told to write a query that "breaks down the answer for charting." Two consequences:

1. **Two database round trips per chart-eligible turn.** The answer query runs in the executor; the chart query runs again in visualization.
2. **Scalar questions still get charted when sensible.** "What was last month's revenue?" → answer is the scalar, chart is the daily breakdown of last month. If no sensible breakdown exists ("how many active users right now?"), the loop exhausts and `chart_json` ends up `null`.

A re-implementer who tries to reuse the answer query for charting will lose this behavior and produce a lot of useless single-bar charts.

---

## 5. Chart-Spec LLM Call

### 5.1 Inputs given to the LLM

In the user message:
- The original user question.
- The exact SQL the answer node ran (`generated_sql`).
- Today's date as `YYYY-MM-DD` (so the LLM can write relative date filters like "last month").
- The schema text (same one the answer generator sees).
- On retries: the previous failed chart SQL **and** the error string.

In the system message:
- Output contract: a single JSON object with `query`, `chart_type`, `title`.
- Chart-type rules (see §5.2).
- SQL rules (see §5.3).
- "Return ONLY the JSON object. No explanation, no markdown, no code fences."
- One worked example showing daily grouping with `strftime` / `date()` modifiers.

### 5.2 Chart-type selection rules (in the prompt)

The LLM is told to pick from `bar | line | pie | area | scatter` based on shape:
- `line` — time series (data over days/months/years).
- `bar` — comparing categories (locations, products, statuses).
- `pie` — composition / share, **max 6–8 slices**. (Advised in the prompt; not enforced anywhere — see §11.)
- `area` — cumulative or stacked time series.
- `scatter` — correlation between two numerics.

### 5.3 SQL rules (in the prompt)

- Must return at least 2 rows.
- Must have one label/date column and one numeric column.
- For single-month answer queries → `GROUP BY date(column)` to produce daily rows.
- For multi-month answer queries → `GROUP BY strftime('%Y-%m', column)`.
- For non-temporal answer queries → group by a category column.
- Dialect-specific date syntax is currently embedded here. **In a re-implementation, move this to a `dialect_prompts/{dialect}.md` snippet keyed off `datasource.dialect`** so the system prompt itself stays source-agnostic.

### 5.4 Parsing the response

Strip whitespace. If wrapped in triple backticks (with or without a `json` tag), extract the inner block. `json.loads`. The result is valid only if it is an object and `query` is present. Strip trailing semicolons from `query` before validating it.

---

## 6. Validation Pipeline

The chart SQL is **not** trusted just because the LLM wrote it. It goes through the same two checks as the answer SQL:

1. **Safety validator** — same regex blocklist used for the answer query: `INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|ATTACH|DETACH|PRAGMA|EXEC|EXECUTE|REPLACE`, no semicolons except trailing, must start with `SELECT` or `WITH`. The validator strips string literals before matching to avoid false positives.
2. **Table ACL check** — extract referenced tables with a regex on `FROM` / `JOIN`, compare against the active context's `visible_tables` set. Any unauthorized table = reject.

A failure at either step counts as one of the three attempts and the **reason string** is fed back to the LLM in the retry prompt.

---

## 7. Self-Correction Loop

- Hard cap: `MAX_CHART_ATTEMPTS = 3`.
- On any failure (parse, validation, ACL, execution, chart-shaping), the loop appends:
  ```
  Your previous chart spec was rejected:
    SQL: <failed_sql>
    Error: <error_message>
  Write a different query that fixes this error.
  ```
  to the next user message.
- **The failed SQL must be in the retry prompt**, not just the error string. The model cannot fix what it cannot see.
- Token usage is accumulated across all attempts (see §10), not only the successful one.

---

## 8. Chart-Shaping Function (`build_auto_chart` equivalent)

A pure function that turns query columns + rows into the final chart JSON. No I/O, no LLM. Signature:

```python
def build_auto_chart(
    columns: list[str],
    rows: list[list],
    chart_preferences,           # may be None
    chart_type: str | None,      # from LLM
    title: str | None,           # from LLM
) -> ChartBuildResult           # {chart: dict | None, error: str | None}
```

### 8.1 Validation it performs (and rejects on)

- Fewer than 2 columns → reject.
- Fewer than 2 rows → reject.
- No detectable numeric column → reject.
- After null-filtering (see §8.3), fewer than 2 rows remain → reject.

A rejection here returns `error` and the loop retries. So shape-time validation is part of the self-correction signal, not just a render-time guard.

### 8.2 Column selection

- **Label column**: first non-numeric column.
- **Value column**: a numeric column, preferring one that comes after the label column.
- **Numeric detection**: a column is numeric if its non-null sample values parse as numbers.

### 8.3 Row cleaning

- Iterate rows; **drop any row where the label or value is `None`**. (This is what can shrink the row count below 2 and cause a retry.)
- Coerce labels to `str` so numeric/date labels become category strings.
- Coerce values to numbers.

### 8.4 Auto chart-type fallback

If the LLM-supplied `chart_type` is missing or not in the valid set:
- Detect time series by **keyword match on the label column name**: `date | month | week | year | day | time | period`.
- If time series → `line`. Otherwise → `bar`.

This is keyword-based, not semantic. A column named `monthly_revenue` will be treated as time-series even though it isn't. A column named `period_id` will too. Document this so re-implementers don't quietly "improve" it into an incompatibility.

### 8.5 Title default

If the LLM omits `title`, generate `"{value_col} by {label_col}"` with underscores → spaces and title-cased.

### 8.6 Output JSON shape (the frontend contract)

```json
{
  "chartType": "bar | line | pie | area | scatter",
  "title": "string",
  "data": [{"<label_col>": "...", "<value_col>": 0}, ...],
  "xAxis": "<label_col>",
  "yAxis": "<value_col>",
  "xLabel": "Title-Cased Label",
  "yLabel": "Title-Cased Value",
  "colors": ["#hex", "#hex", ...]
}
```

The frontend uses:
- Cartesian charts (bar/line/area/scatter): `xAxis.dataKey = xAxis`, series `dataKey = yAxis`, `colors[0]` for the primary series.
- Pie: `dataKey = yAxis`, `nameKey = xAxis`, colors cycled per slice via `<Cell>`.
- Container height is hardcoded on the frontend (300px today).

---

## 9. Configuration: `chart_preferences`

Per-context YAML block:

```yaml
chart_preferences:
  default_type: bar                       # CURRENTLY UNUSED — see §11
  color_palette: ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#65a30d"]
  guidelines: |                           # CURRENTLY UNUSED — see §11
    Use bars for category comparisons.
    Use lines for trends.
```

Only `color_palette` is consumed by the chart-shaping function. `default_type` and `guidelines` are loaded but never read. A re-implementation should either (a) wire them up — pass `default_type` as the fallback chart type and inject `guidelines` into the chart system prompt — or (b) remove them. Leaving them in as decoration is a footgun.

---

## 10. Parallel Execution & State

After the executor produces `raw_data`, the graph fans out to `[visualization, strategist]` in parallel. Both branches return updates to a shared state.

**Critical**: `token_usage` in the state must use a list-concat reducer (in LangGraph: `Annotated[list, add]`). Without this, the parallel branches will overwrite each other's usage and one of them silently disappears from the per-turn total.

Other state fields written by visualization:
- `chart_json: dict | None`

Visualization never sets `error`. A failed chart loop returns `chart_json: None` and that's it; the answer path is unaffected.

---

## 11. Failure Modes

| # | Failure | Behavior |
|---|---|---|
| 1 | LLM call raises | Logged, counts as one attempt, retry. |
| 2 | LLM returns unparseable JSON | Retry with `error="LLM returned empty/unparseable response"`. |
| 3 | Chart SQL rejected by safety validator | Retry with the validator's reason in the prompt. |
| 4 | Chart SQL references unauthorized table | Retry with the offending table list in the prompt. |
| 5 | Chart SQL execution error / timeout | Retry with the driver error string in the prompt. |
| 6 | Chart SQL returns < 2 rows / no numeric column / all-null | Retry with shape error. |
| 7 | All 3 attempts exhausted | Return `chart_json: None`. **No `error` field is set; user sees no indication.** |
| 8 | `raw_data` missing on entry | Return immediately with `chart_json: None`, no LLM call. |

The combination of (3) and (4) means a re-implementer's safety/ACL layers must produce machine-friendly reason strings, not opaque booleans, or the self-correction loop has nothing to learn from.

---

## 12. Frontend Contract

The chart spec lands on the frontend as part of the chat response. The renderer:
- Switches on `chartType`.
- For cartesian charts: `<XAxis dataKey={xAxis} label={xLabel} />`, `<YAxis label={yLabel} />`, series with `dataKey={yAxis}` and `stroke|fill={colors[0]}`.
- For pie: `<Pie data={data} dataKey={yAxis} nameKey={xAxis} />` with `<Cell fill={colors[i % colors.length]} />` per slice.
- Renders nothing if `chart_json` is null.

If you change the JSON shape on the backend, you must change the frontend renderer in lockstep — there is no version negotiation.

---

## 13. Data-Source Abstraction (preserving §7 of `backend/PRD.md`)

The chart subsystem currently hardcodes SQLite syntax in the chart system prompt (`date()`, `strftime()`, modifier list). To make this source-agnostic:

- Move all dialect-specific snippets into `dialect_prompts/{dialect}.md` and splice into the chart system prompt at runtime based on `datasource.dialect`.
- The chart query goes through the same `DataSource.execute_query(sql, timeout_s, max_rows)` interface as the answer query — no bespoke connection.
- The 2-row minimum, the chart-type rules, the JSON output contract, and the self-correction structure are all dialect-agnostic and should not change per source.

---

## 14. Non-Obvious Behaviors (read this section carefully)

These are the things that bite re-implementers.

1. **Two SQL executions per turn.** Budget for it. If your data warehouse charges per query, this doubles chart-eligible turn cost.
2. **Silent chart failure.** No error in the API response, no toast on the frontend. The user just doesn't see a chart. If you want to surface "couldn't chart this" you have to add it deliberately.
3. **Token cost accumulates across retries.** A turn that retries the chart 3 times bills the user for 3 LLM calls plus the answer call plus the strategist call. Make the per-turn usage object reflect the sum of all calls, not just the successful one.
4. **The chart query is bounded by `max_rows` (500 default) independently of the answer query.** A chart query that hits the cap may produce a misleading visualization with no warning.
5. **Table-ACL is checked twice.** Once in the safety validator (for the answer query path; generic) and once explicitly in visualization (against `visible_tables`). The double-check is intentional — keep it.
6. **`chart_preferences.default_type` and `guidelines` are loaded but never used.** Either wire them up or delete them in your re-implementation; do not copy them as-is.
7. **Time-series detection is keyword-based on the column name.** `monthly_revenue` triggers `line` even when it isn't temporal; `period_id` does too. This is not a bug to fix without thinking — fixing it changes user-visible behavior.
8. **Labels are coerced to strings.** Date and numeric labels lose their type. The frontend must not assume otherwise.
9. **Pie slice cap is advisory.** The system prompt says "max 6–8" but nothing enforces it. If the LLM returns 50 categories you get a 50-slice pie. Add a server-side cap if this matters.
10. **Strategist (the parallel sibling) is resilient; visualization is silent.** Asymmetric failure handling. Don't accidentally "unify" them by making strategist failures crash the response.
11. **The chart system prompt explicitly forbids markdown / code fences,** but the parser still tolerates them. Keep the tolerant parser — small models violate the instruction often.
12. **The retry prompt must include the failed SQL, not just the error.** This is repeated everywhere in the codebase for a reason. Models cannot self-correct from the error alone.

---

## 15. Reimplementation Milestones

1. **Skeleton node**: takes `raw_data` + `user_question` + `generated_sql` + schema, calls the LLM with the chart system prompt, parses JSON, returns the spec verbatim. No retries, no validation. Good enough to verify the prompt works end-to-end.
2. **Add safety + ACL validation** with reason strings.
3. **Add execution** through the `DataSource` interface.
4. **Add `build_auto_chart`** (column selection, null-filtering, auto chart-type fallback, JSON shape).
5. **Wire the self-correction loop** with `MAX_CHART_ATTEMPTS = 3` and the failed-SQL-included retry prompt.
6. **Wire token accounting** through the parallel-safe reducer.
7. **Move dialect snippets out of the prompt** into `dialect_prompts/{dialect}.md`.
8. **Frontend renderer** — Recharts component switching on `chartType`, with the contract from §8.6.

---

## 16. Open Questions

- Should chart failures be surfaced to the user (e.g. `chart_error: "could not chart this answer"`) or kept silent?
- Should we cap pie slices server-side, or trust the prompt?
- Should the chart query be allowed to JOIN tables the answer query didn't, as long as they're in `visible_tables`? (Today: yes.)
- For sources that can return a chart-ready aggregation cheaply (e.g. cube / OLAP), do we want a fast path that skips the second LLM call?
- Should `chart_preferences.guidelines` be injected into the chart system prompt? It would let context authors steer chart-type choice without code changes.
