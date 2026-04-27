# Backend PRD — Conversational Analytics Service

## 1. Overview

A chat backend that turns natural-language business questions into one of three structured answers:

1. **SQL answers** — generate, validate, and execute an analytical query against a tabular data source, then produce a chart spec and follow-up suggestions.
2. **Docs answers** — look up definitions, policies, and glossary terms in a corpus of markdown files.
3. **Lineage answers** — describe where a metric, column, or table comes from, using a curated lineage file.

The LLM is the reasoning layer. It classifies intent, generates SQL, self-corrects on errors, and composes natural-language answers. The backend's job is to assemble the right context, enforce safety, and expose a clean API to the frontend.

A reimplementation of this backend must **not** assume SQLite. The data source is a pluggable adapter, so the same service can sit on top of Postgres, BigQuery, Snowflake, DuckDB, or a read-only REST API without changing the graph, the router, or the API contract.

---

## 2. Goals & Non-Goals

**Goals**
- Data-source-agnostic: any backing store that can answer "execute this read-only query and return rows" works.
- LLM-provider-agnostic: a single `chat_completion` seam; OpenAI-compatible by default.
- Stateful, server-side sessions (the client only sends `session_id` + new message).
- Self-correcting SQL loop: failed queries + their errors are fed back to the LLM.
- Structured, frontend-ready responses: SQL text, rows, a Recharts-shaped chart spec, follow-up suggestions, or a composed `answer_text` for doc/lineage paths.
- Read-only by construction: defense in depth at both the adapter and validator layers.

**Non-Goals**
- Authentication / authorization (assumed handled by a fronting gateway).
- Durable session persistence (in-memory is acceptable for v1).
- Multi-tenant data isolation beyond per-context `visible_tables`.
- Embedding/vector retrieval for docs — token-overlap ranking is a deliberate simplification.
- Response streaming — the API is a single JSON response per turn.

---

## 3. Users & Use Cases

| User intent | Example question | Route | Response shape |
|---|---|---|---|
| "Give me a number / trend" | *"What was net revenue last quarter by region?"* | `sql` | `sql`, `raw_data`, `chart_json`, `suggestions` |
| "Define a term / policy" | *"What counts as net revenue?"* | `docs` | `docs_results`, `answer_text` |
| "Where does X come from?" | *"What's the formula behind the `net_revenue` metric?"* | `lineage` | `lineage_node`, `answer_text` |

---

## 4. API Contract

All responses are `application/json`. Errors return a populated `error` field and HTTP 200 unless the request itself is malformed.

### `POST /api/chat`

**Request**
```json
{
  "message": "string",
  "session_id": "string | null",
  "context_id": "string | null"
}
```
- `session_id` is optional on the first turn; the server mints one and returns it.
- `context_id` is required when starting a new session; ignored afterward.

**Response**
```json
{
  "session_id": "string",
  "question_type": "sql | docs | lineage",

  "sql": "string | null",
  "raw_data": {
    "columns": ["..."],
    "rows": [["..."]],
    "row_count": 0,
    "truncated": false,
    "execution_time_ms": 0
  } ,
  "chart_json": { "...recharts spec..." },
  "suggestions": ["string", "..."],

  "docs_results": [
    { "path": "string", "title": "string", "snippet": "string", "content": "string" }
  ],

  "lineage_node": {
    "kind": "metric | column | table",
    "name": "string",
    "formula": "string | null",
    "upstream_tables": ["string"]
  },

  "answer_text": "string | null",

  "usage": {
    "turn":    { "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_calls": 0 },
    "session": { "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_calls": 0 }
  },

  "error": "string | null"
}
```

Only the fields relevant to `question_type` are populated; the rest are `null` / absent.

### `GET /api/contexts`
Returns the list of available contexts (id, name, description) that the frontend can offer as a starting selection.

### `GET /api/health`
Liveness probe; returns `{"status": "ok"}`.

### Session semantics
- The backend owns the full conversation history per `session_id`.
- The frontend **never** resends prior messages — only the new user message.
- Session history is appended on both the user turn and the assistant turn (SQL text / answer_text + any structured payload summary that should remain visible to future turns).

---

## 5. High-Level Architecture

```
          ┌────────────────────────────────────────────────────────────┐
 HTTP ──▶ │ FastAPI (POST /api/chat)                                   │
          │   ├─ SessionStore.get_or_create(session_id, context_id)    │
          │   ├─ invoke LangGraph workflow with initial state          │
          │   └─ serialize final state → ChatResponse                  │
          └────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                      ┌────────────────────┐
                      │ context_gatherer   │  loads context YAML + schema from DataSource
                      └────────┬───────────┘
                               ▼
                      ┌────────────────────┐
                      │ router (LLM)       │  → {type, subject}
                      └─┬────────┬────────┬┘
                        │        │        │
                   sql  │   docs │  lineage│
                        ▼        ▼        ▼
             ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
             │ sql_generator│ │ docs_lookup  │ │ lineage_lookup   │
             └──────┬───────┘ └──────┬───────┘ └─────────┬────────┘
                    ▼                ▼                   ▼
             ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
             │ validator    │ │ docs_answer  │ │ lineage_answer   │
             └──────┬───────┘ └──────┬───────┘ └─────────┬────────┘
              fail │  ok             │                   │
              ▲    ▼                 │                   │
              │  ┌──────────────┐    │                   │
              └──│ executor     │    │                   │
                 └──────┬───────┘    │                   │
              fail ◀── │ ok         │                   │
                       ▼             │                   │
            ┌──────────────┬─────────┴───────┐           │
            │ visualization│ strategist      │ (parallel)│
            └──────┬───────┴────────┬────────┘           │
                   └───────┬────────┘                   │
                           ▼                             ▼
                         END                            END
```

The SQL loop retries generation up to `sql.max_retries` times, feeding the failed SQL + error back into the generator each time.

---

## 6. Core Components

### 6.1 SessionStore
In-memory map of `session_id → Session`. `Session` holds:
- `id: str`
- `context_id: str`
- `messages: list[{role, content}]`
- token counters (`prompt_tokens`, `completion_tokens`, `llm_calls`) accumulated across the whole session

Replaceable with Redis or Postgres later; no code outside `SessionStore` should touch session storage directly.

### 6.2 Router
A single LLM call that classifies each turn. The prompt enumerates the three categories with examples and demands a strict JSON response: `{"type": "sql|docs|lineage", "subject": "..."}`.

- `subject` for `docs` is a free-text search query.
- `subject` for `lineage` is a canonical metric/column/table name.
- `subject` for `sql` is unused but may carry a one-line restatement of the question.

On parse failure, the router falls back to `sql`. **Do not** add Python-side regex heuristics to "help" the router — if classification is wrong, fix the prompt.

### 6.3 ContextManager
Loads per-context YAML at startup. Each context defines:
- `id`, `name`, `description`
- `system_prompt` — business-domain framing
- `visible_tables` — ACL list for this context
- `metrics` — named metric definitions injected into the SQL system prompt
- `chart_preferences` — hints to the visualization node

### 6.4 DocStore
Loads `docs/*.md` at startup; each doc's first heading becomes its title.

`search(query)` tokenizes the query and scores each doc by `title_hits × 3 + body_hits × 1`, returning the top 3. Results carry the full markdown body for injection into the answer prompt.

No embeddings, no vector DB. This is intentional — keep it simple until relevance complaints force otherwise.

### 6.5 LineageStore
Loads a single `lineage.yaml` with `metrics:`, `columns:`, `tables:` sections. Lookups are case-insensitive.

On miss, the lineage_lookup node returns a catalog of known names so the answer node can say "I don't have lineage for X, but I know about Y and Z."

### 6.6 DataSource adapter
See §7. This is the only component that must be re-implemented per backing store.

### 6.7 LLMClient
Minimal surface:
```python
class LLMClient(Protocol):
    async def chat_completion(
        self, messages: list[Message], **kwargs
    ) -> ChatCompletion: ...
```
`ChatCompletion` must expose `.content: str` and `.usage: {prompt_tokens, completion_tokens}`. The default implementation wraps any OpenAI-compatible endpoint (Ollama, vLLM, OpenAI, Azure OpenAI). Swapping in Anthropic / Bedrock / Vertex is a new class implementing the same protocol.

### 6.8 SQL loop
1. **Generator** — LLM call with `system_prompt + session_messages + user_question`. On retry, the prompt includes the previous failed SQL **and** the error. The failed output must be in the prompt — "Error: timeout" alone is not enough for the model to self-correct.
2. **Validator** — regex-level rejection of `INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|ATTACH|VACUUM|PRAGMA`; plus table-ACL check against `context.visible_tables` by parsing referenced table names.
3. **Executor** — calls `datasource.execute_query(sql, timeout_s, max_rows)`. Returns `QueryResult`.
4. On validation or execution error, if `sql_attempts < max_retries`, loop back to generator with the error fed in.

### 6.9 Visualization & Strategist (parallel fan-out)
After a successful query, two nodes run in parallel:
- **Visualization** — LLM proposes a chart type and dimensions given `chart_preferences` and the result shape; output is Recharts-compatible JSON.
- **Strategist** — LLM proposes up to 3 follow-up questions the user is likely to ask next.

Both write to `state.token_usage` via LangGraph's `add` reducer so usage accumulates correctly across the parallel branch.

---

## 7. DataSource Abstraction

This is the **heart of the rewrite**. Everything data-source-specific in the current codebase must move behind this interface.

### 7.1 Protocol

```python
from typing import Protocol
from dataclasses import dataclass

@dataclass
class Column:
    name: str
    type: str
    nullable: bool
    description: str | None = None

@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]           # JSON-serializable cell values
    row_count: int
    truncated: bool
    execution_time_ms: int

class DataSource(Protocol):
    dialect: str               # "postgres" | "sqlite" | "bigquery" | "snowflake" | "rest" | ...

    async def list_tables(self) -> list[str]: ...
    async def get_columns(self, table: str) -> list[Column]: ...
    async def execute_query(
        self,
        sql: str,
        *,
        timeout_s: float,
        max_rows: int,
    ) -> QueryResult: ...
    async def close(self) -> None: ...
```

### 7.2 Read-only enforcement (defense in depth)
- **Adapter layer** — the driver itself is configured read-only:
  - SQLite: `PRAGMA query_only = ON` on every connection.
  - Postgres: a role that has only `SELECT` grants, plus `SET TRANSACTION READ ONLY` per request.
  - BigQuery: a service account with `roles/bigquery.dataViewer` only.
  - Snowflake: a role with usage-only grants on the warehouse/database.
  - REST: no write verbs exposed by the adapter.
- **Validator layer** — the regex blocklist in the validator runs regardless of dialect. Both layers must allow the query for it to execute.

### 7.3 Dialect-specific prompt snippets
Functions like `date()` / `strftime()` (SQLite) vs `DATE_TRUNC()` (Postgres) vs `TIMESTAMP_TRUNC()` (BigQuery) must not be hardcoded in `context_gatherer`. Options:

- **(a)** Store dialect hints in the context YAML under a `dialect_notes:` key.
- **(b) Preferred** — keep a `dialect_prompts/{dialect}.md` directory. The context_gatherer reads the snippet matching `datasource.dialect` and splices it into the system prompt.

Either way, `context_gatherer` itself stays dialect-free.

### 7.4 Connection config
Replace the current `database_path: str` with a structured block:
```yaml
datasource:
  type: postgres           # maps to an adapter factory
  dsn: postgresql://readonly_user:***@host:5432/analytics
  options:
    application_name: ai-harness
    connect_timeout: 5
```
A factory (`build_datasource(config) -> DataSource`) picks the adapter. Every adapter is responsible for applying read-only settings from its `options` during connection setup.

### 7.5 Schema introspection
Each adapter exposes `list_tables()` and `get_columns()`. The graph never sees raw `information_schema` or `sqlite_master` queries — those live inside the adapter.

---

## 8. LLM Provider Abstraction

- One interface: `LLMClient.chat_completion(messages, **kwargs)`.
- All node code takes an `LLMClient` by dependency injection; no node imports `openai` or `ollama` directly.
- Usage extraction is uniform: every adapter must return `{prompt_tokens, completion_tokens}` so token_usage accumulates correctly.
- Temperature, max_tokens, and similar knobs are kwargs passed through.

Swapping providers = writing a new class; no other code changes.

---

## 9. Graph State Schema

The LangGraph state is a single TypedDict threaded through every node. Only the node listed in "Written by" should mutate a given field (except `token_usage`, which uses `add` for parallel accumulation).

| Field | Type | Written by | Used by |
|---|---|---|---|
| `user_question` | str | HTTP layer | router, sql_generator, docs_lookup, lineage_lookup |
| `context_id` | str | HTTP layer | context_gatherer |
| `session_messages` | list[Message] | HTTP layer | sql_generator, docs_answer, lineage_answer |
| `system_prompt` | str | context_gatherer | sql_generator |
| `schema_text` | str | context_gatherer | sql_generator |
| `context_config` | ContextConfig | context_gatherer | validator (for `visible_tables`), visualization |
| `question_type` | "sql"\|"docs"\|"lineage" | router | routing |
| `routing_subject` | str | router | docs_lookup, lineage_lookup |
| `generated_sql` | str | sql_generator | validator, executor |
| `validation_error` | str\|None | validator | sql_generator (retry) |
| `execution_error` | str\|None | executor | sql_generator (retry) |
| `sql_attempts` | int | sql_generator | routing |
| `raw_data` | QueryResult | executor | visualization, strategist, HTTP layer |
| `chart_json` | dict | visualization | HTTP layer |
| `suggestions` | list[str] | strategist | HTTP layer |
| `docs_results` | list[DocResult] | docs_lookup | docs_answer, HTTP layer |
| `lineage_node` | LineageNode\|None | lineage_lookup | lineage_answer, HTTP layer |
| `lineage_known` | dict | lineage_lookup | lineage_answer (on miss) |
| `answer_text` | str\|None | docs_answer, lineage_answer | HTTP layer |
| `token_usage` | list[Usage] (reducer: `add`) | every LLM node | HTTP layer |
| `error` | str\|None | any node | HTTP layer |

Conditional edges:
- After `context_gatherer`: `error` set → END; else → `router`.
- After `router`: dispatch on `question_type`.
- After `validator`: `validation_error` and `sql_attempts < max_retries` → back to `sql_generator`; else → `executor` or END.
- After `executor`: `execution_error` and `sql_attempts < max_retries` → `sql_generator`; `raw_data` present → fan out to `[visualization, strategist]`; else → END.

---

## 10. Configuration

Env-driven via pydantic-settings. Grouped:

```
llm.base_url            # e.g. http://localhost:11434/v1
llm.model               # e.g. qwen2.5:3b
llm.api_key             # "ollama" for local, real key for hosted

datasource.type         # postgres | sqlite | bigquery | snowflake | rest
datasource.dsn          # connection string / URI
datasource.options      # free-form dict of adapter-specific options

sql.query_timeout_s     # default 30
sql.max_rows            # default 500
sql.max_retries         # default 3

paths.contexts_dir      # app/contexts
paths.tables_dir        # app/tables
paths.docs_dir          # app/docs
paths.lineage_file      # app/lineage.yaml
```

Nothing else should be configurable via env in v1.

---

## 11. Non-Functional Requirements

- **Query timeout**: every `execute_query` call wrapped in `asyncio.wait_for(..., timeout_s)`. Timeouts surface as `execution_error`, triggering retry.
- **Row cap**: adapters must respect `max_rows` and set `truncated=True` when the cap is hit.
- **Retry cap**: `sql.max_retries` upper-bounds the self-correction loop.
- **Read-only guarantee**: enforced at both adapter and validator layers.
- **Token accounting**: every LLM call contributes to `state.token_usage`; the HTTP layer aggregates per-turn and updates per-session totals in `SessionStore`.
- **Error hygiene**: never return raw driver tracebacks to the client. Map known error classes (timeout, syntax, permission, connection) to short, user-safe strings in `error`.
- **Startup cost**: all context / docs / lineage loading happens once at process start, not per request.

---

## 12. Invariants / Design Rules

A reimplementation must preserve these — they are load-bearing.

1. **LLM owns reasoning, not Python.** No regex intent classifiers, no hardcoded response generators. If classification is wrong, fix the prompt, not the Python.
2. **Prompt over code for behavior changes.** Wrong SQL → improve the system prompt or context, don't add Python preprocessors.
3. **Don't short-circuit the LLM loop.** The LLM should produce text responses; the orchestrator must not return early with hardcoded summaries or suggestions.
4. **Backend owns session state.** The client sends only `session_id` + new message.
5. **Self-correction loops include the failed output.** `"Your previous SQL: {sql}\nError: {err}\nWrite a corrected query."` — not just `"Error: {err}"`.
6. **Carry forward established patterns when rewriting.** Don't strip session management, retry feedback, parallel fan-out, or the validator in a "simplified" rewrite.

---

## 13. Milestones for a Re-Implementation

A suggested order that keeps the system demoable at every step.

1. **HTTP skeleton + SessionStore + `/health`** — empty graph, echo response.
2. **LLMClient + Router + docs path end-to-end** — simplest full loop; validates the session + routing + structured-response plumbing.
3. **DataSource interface + one adapter (Postgres recommended) + SQL generator/validator/executor with self-correction** — the analytical heart.
4. **Visualization + Strategist parallel fan-out** — proves the state reducer and parallel token accumulation.
5. **Lineage path** — mirrors docs; small.
6. **Second adapter (BigQuery, Snowflake, or REST)** — proves the abstraction actually works before anyone declares victory.

---

## 14. Open Questions

- **Session persistence layer** — in-memory is fine for v1; Redis likely next. Should sessions have a TTL?
- **Multi-tenant isolation** — today, `visible_tables` per context is the only fence. Do we need row-level policies at the adapter layer?
- **Doc retrieval quality** — token overlap works for a small corpus. At what size do we switch to embeddings, and do we keep token-overlap as a fallback?
- **Streaming responses** — currently a single JSON response per turn. Is SSE / chunked streaming worth the added complexity?
- **SQL dialect translation** — should the LLM generate dialect-specific SQL up front (current approach), or a neutral form that a translator converts? The current approach is simpler and has worked; revisit only if we need to support many dialects simultaneously in one deployment.
