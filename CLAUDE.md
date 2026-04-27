# AI Harness – Development Guidelines

## Architecture Principles

### LLM owns reasoning, not Python
Do not write Python code (regex classifiers, pattern matchers, hardcoded response generators) to do what the LLM should handle via prompting. If the LLM needs to detect user intent (e.g. "trend query" vs "aggregate query"), put that guidance in the system prompt — not in Python regex.

### Prompt over code for behavior changes
When the LLM produces wrong output (e.g. too-narrow SQL filters), fix it by improving the prompt or context injection — not by adding Python preprocessing that manipulates state before the LLM sees it. Exception: self-correction loops where a subagent retries with error feedback are fine.

### Don't short-circuit the LLM loop
Let the LLM generate its own text responses. Don't return early from the orchestrator with hardcoded summaries or follow-up suggestions. The LLM should loop back after tool execution to produce natural, context-aware responses.

### Multi-turn conversation requires server-side sessions
Every backend that serves the chat API must maintain conversation history server-side. The frontend sends only `session_id` + new message — never the full message array. When building a new backend or rewriting an existing one, carry over the session store pattern from the existing implementation.

### Self-correction loops must include the failed output
When an LLM retry loop feeds an error back to the model (e.g. SQL validation failed, execution error), always include the output that caused the failure alongside the error message. The LLM cannot fix what it cannot see. Pattern: `"Your previous SQL: {failed_sql}\nError: {error}\nWrite a corrected query."` — not just `"Error: {error}"`.

### Carry forward established patterns when building new services
When creating a rewrite or parallel implementation of an existing service, audit the original for patterns that must survive: session management, retry feedback, error handling, streaming contracts, etc. Don't build a "simplified" version that drops load-bearing behavior.

## Project Setup

- Python venv lives at `backend/venv` — use it, don't create new ones
- Docker: `docker compose up` runs backend, frontend, and ollama
- The backend is a LangGraph workflow over Ollama; chat endpoint is `POST /api/chat` (single JSON response, not streaming)

## Learnings

- **LangGraph `Send` only injects state into its first destination.** Subsequent nodes via `add_edge` don't see Send-payload-only fields like `_current_subtask`. For per-subtask scope across a chain, collapse the chain into a single runner node. See `backend/app/graph/nodes/subtask_runners.py` and `docs/sessions/2026-04-26-multi-subtask-workflow.md`.
- **LangGraph drops state fields not declared on the `GraphState` schema.** Pass workflow config (e.g. `max_retries`) via closure at build time, not by mutating the input state dict.
- **For multi-write list state in LangGraph, use a custom merge-by-id reducer**, not `operator.add`. Append-style reducers create duplicate entries when retry loops re-enter the same logical record. See `merge_subtasks_by_id` in `backend/app/graph/state.py`.
- **Validator regex flagged CTEs as unauthorized tables.** SQL access checks must extract CTE names (`\b(\w+)\s+AS\s*\(\s*(?:SELECT|WITH|VALUES)\b`) and exclude them before comparing references against `visible_tables`. Same pattern lives in `validator.py` and `visualization.py`.
- **Validator errors must echo the allowed-tables list.** Without it, small models just hallucinate a different fake name on retry.
- **Synthesizer prompts must explicitly forbid bracket-style placeholders** (`[X]`, `[amount from s1]`, `[query result]`). Models will echo bracket conventions back as fillable templates if the format uses them. Use natural-language labels (`Subtask 1`) and `STATUS: OK/FAILED` lines instead.
- **After editing backend code, redeploy with `docker compose up -d --build --force-recreate backend`.** Plain `up` (even with `--build`) often keeps the old container alive. Verify with `docker exec ai-harness-backend-1 grep <signature> /app/...` before debugging behavior.
- **Default model is `qwen2.5:3b` — small and frequently emits Postgres syntax** despite explicit "this is SQLite" rules in the system prompt. Targeted retry hints in `sql_generator.py` cover the common offenders (`::` casts, `DATE_TRUNC`/`EXTRACT`/`NOW`/`INTERVAL`, `:name` params, `= ANY`/`= ALL`, UNION column-count). Stronger models (`qwen2.5:7b+`) reduce the need for these dramatically.
- **Hide scaffolding subtask types in three places.** When adding an internal-only subtask type (e.g. `investigate`), filter it out of (1) synthesizer rendering, (2) strategist input, and (3) the API artifact builder. Skipping any one leaks scaffolding into either the user-facing answer, follow-up suggestions, or next-turn session history. See `INVESTIGATION_PRD.md` and `docs/sessions/2026-04-26-investigation-subtask.md`.
- **Round-budget-bound subtask types need a Python coercion backstop.** Investigation is round-1-only because round 2 is the answer round. The prompt forbids round-2 investigations, but small models drift — `planner.py` downgrades any round-2 `investigate` to `sql` with a warning so the user still gets an answer instead of a hidden subtask.
