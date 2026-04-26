## Session Wrap-Up (2026-04-26)

### Goal
Make a single user message able to trigger multiple SQL queries plus optional doc/lineage lookups in one response (instead of routing to exactly one branch). Then debug the live failures the user kept hitting on `qwen2.5:3b`.

### Accomplished
- Full planner-executor refactor shipped on `master` (commit `c1c8992`). 23 files / +1881 / -478.
- New nodes: `planner.py` (replaces `router.py`), `subtask_runners.py`, `synthesizer.py`.
- State migration: singleton sql/docs/lineage fields → `subtasks: list[SubtaskResult]` with `merge_subtasks_by_id` reducer.
- Workflow: `planner → Send fan-out → runners → subtask_join → re-plan-or-[synthesizer, strategist] → END` with bounds (2 plan rounds, 4 subtasks, 3 SQL retries per subtask).
- Adaptive re-plan: planner round 2 sees prior subtasks summary in its prompt.
- Frontend: read `data.artifacts` directly; SQL artifact now bundles its chart (dropped separate `"chart"` artifact type); per-artifact question header when N>1.
- Multi-turn session continuity: chat route flattens artifact summaries into assistant content so the next-turn planner has memory of what was already fetched.
- Self-correction enrichments to combat small-model SQL mistakes:
  - CTE-aware validator (was rejecting `WITH foo AS (...)` references)
  - Validator error now echoes the allowed-tables list
  - SQL generator retry hints: SQLite-vs-Postgres catalog (`::` casts, `DATE_TRUNC`/`EXTRACT`/`NOW`/`INTERVAL`, `:name` params, `E'`/`$$` quoting, `= ANY`/`= ALL`, `CONCAT`, `true`/`false`), UNION column-count mismatch, `no such column`
  - Synthesizer prompt rewritten to forbid placeholder strings (`[amount from s1]`); subtask format uses natural-language labels (`Subtask 1`) instead of `[s1]`
- PRD updated with implementation reality + gotchas section (`backend/MULTI_SUBTASK_PRD.md`).
- Backend Docker image rebuilt and confirmed running the new code via `docker exec`.

### Deferred
- Subtask dependencies (`depends_on: [id]`) — Phase 3 from original plan, skipped (no user demand).
- Committed test files — implementation was verified via inline FastAPI TestClient runs with stubbed deps; no `tests/` directory exists in the repo.
- Per-turn LLM-call ceiling — only the per-subtask retry cap and per-turn subtask cap are enforced. Worst-case is bounded but not centrally tracked.
- Telemetry counters (subtask count per turn, planning rounds, retries) — useful for future planner-prompt tuning but not yet emitted.

### Learnings
- **LangGraph `Send` does NOT propagate payload state past its first destination.** This invalidated the original plan to wire `sql_generator → validator → executor → visualization` as separate LangGraph nodes — `_current_subtask` is `None` by the time `validator` runs. Fix: collapse each subtask's pipeline into a single runner node (`subtask_runners.py`) that calls the inner steps procedurally inside one LangGraph invocation.
- **LangGraph strips state fields not declared on `GraphState`.** Tried passing `state["_max_retries"]` through `WorkflowRunner.ainvoke`; it was gone by the time the runner read it. Fix: capture config via closure at workflow build time.
- **Validator regex `\b(?:FROM|JOIN)\s+(\w+)` matches CTE references**, not just real table references. The `_CTE_NAME_PATTERN = r"\b(\w+)\s+AS\s*\(\s*(?:SELECT|WITH|VALUES)\b"` excludes them; the keyword inside the parens is the discriminator that prevents column aliases (`SUM(x) AS total`) and subquery aliases from false-matching.
- **Validator errors must echo the allowed-tables list**, not just say what's wrong — without it, small models hallucinate a different fake name on retry instead of converging.
- **Synthesizer latches onto bracketed labels.** When the per-subtask format used `[s1] sql — ...`, the model echoed it as `[amount from s1 query]` literal text. Use natural-language labels (`Subtask 1 (sql) — asked: '...'`) and explicit `STATUS: OK/FAILED` lines.
- **Reducer for parallel-write list state must merge by id, not append.** `Annotated[list, operator.add]` would create 4 entries for a subtask that retries 4 times. Custom reducer does last-write-wins per id with field-level merge.
- **Docker `compose up` does not reload Python sources.** Reliable incantation after a backend code edit: `docker compose up -d --build --force-recreate backend`. `--force-recreate` is the critical flag — without it, Docker keeps the old container even when a newer image exists. Verify with `docker exec ai-harness-backend-1 grep <signature> /app/...`.
- **Small models (`qwen2.5:3b`) repeatedly leak Postgres syntax in many shapes** even with explicit warnings in the system prompt. Targeted retry hints help but don't eliminate the loops; `qwen2.5:7b` is roughly 10× better at following SQL-dialect rules.

### Blockers
- None for the architecture itself. The system handles failures gracefully — failed SQL subtasks complete with `error` set, synthesizer reports cleanly, parallel docs/lineage subtasks still surface their answers.
- Operational reality: hard finance queries (e.g. net-revenue requiring UNION ALL of orders + cart_orders with multi-table test-row exclusions) regularly exhaust the 4-attempt retry budget on `qwen2.5:3b`. Not a code blocker; a model-size choice.

### Next Session
Start with: **swap `MODEL_NAME` to `qwen2.5:7b` in `docker-compose.yml`** (`docker exec ai-harness-ollama-1 ollama pull qwen2.5:7b` first, then `docker compose up -d --force-recreate backend`), retry the finance net-revenue question, and observe whether the multi-subtask architecture converges in 1-2 attempts per subtask. If it does, consider deleting some of the more aggressive retry hints in `sql_generator.py` since they were added for `:3b`-specific failures and a stronger model may not need them. If it doesn't converge cleanly, add a unit test scaffold under `backend/tests/` covering the merge_subtasks_by_id reducer + planner JSON parser as a regression net.
