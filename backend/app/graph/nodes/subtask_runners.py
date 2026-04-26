"""Per-subtask runner nodes.

Send fan-out drops state fields after the destination node returns, so the
SQL/docs/lineage pipelines can't be split across multiple LangGraph nodes
without losing `_current_subtask`. Each runner here orchestrates its full
pipeline in a single node, calling the existing inner step functions in
sequence and threading the subtask through them.
"""

from __future__ import annotations

import logging
from typing import Any

from app.graph.nodes.docs_answer import docs_answer_node
from app.graph.nodes.docs_lookup import docs_lookup_node
from app.graph.nodes.executor import executor_node
from app.graph.nodes.lineage_answer import lineage_answer_node
from app.graph.nodes.lineage_lookup import lineage_lookup_node
from app.graph.nodes.sql_generator import sql_generator_node
from app.graph.nodes.validator import validator_node
from app.graph.nodes.visualization import visualization_node
from app.graph.state import GraphState, SubtaskResult

logger = logging.getLogger(__name__)


def _apply_subtask_updates(
    current: SubtaskResult, returned: dict, sid: str
) -> list[dict]:
    """Merge any subtask updates from a child step into `current` (in place).

    Returns the list of token_usage entries from the child step.
    """
    for st_update in returned.get("subtasks", []) or []:
        if st_update.get("subtask_id") == sid:
            for k, v in st_update.items():
                if k != "subtask_id":
                    current[k] = v
    return returned.get("token_usage", []) or []


def sql_subtask_runner_node(
    llm_client: Any,
    sql_engine: Any,
    safety: Any,
    timeout: float = 30.0,
    max_rows: int = 500,
    max_retries: int = 3,
):
    sql_gen = sql_generator_node(llm_client)
    val = validator_node(safety)
    exe = executor_node(sql_engine, timeout, max_rows)
    viz = visualization_node(llm_client, sql_engine, safety, timeout, max_rows)

    async def _run(state: GraphState) -> dict:
        current: SubtaskResult = dict(state.get("_current_subtask") or {})
        sid = current.get("subtask_id", "?")
        token_usage: list[dict] = []

        # Local "scoped state" we feed to each step. We mirror `_current_subtask`
        # and `subtasks` from `current` so the inner nodes see the latest values.
        def scoped() -> dict:
            return {
                **state,
                "_current_subtask": current,
                "subtasks": [current],
            }

        for attempt in range(max_retries + 1):
            # 1. Generate SQL
            ret = await sql_gen(scoped())
            token_usage += _apply_subtask_updates(current, ret, sid)

            # 2. Validate
            ret = await val(scoped())
            _apply_subtask_updates(current, ret, sid)

            if current.get("validation_error"):
                logger.info(
                    "[%s] validation failed (attempt %d/%d): %s",
                    sid, attempt + 1, max_retries + 1, current["validation_error"],
                )
                if attempt < max_retries:
                    continue
                break  # exhausted

            # 3. Execute
            ret = await exe(scoped())
            _apply_subtask_updates(current, ret, sid)

            if current.get("execution_error"):
                logger.info(
                    "[%s] execution failed (attempt %d/%d): %s",
                    sid, attempt + 1, max_retries + 1, current["execution_error"],
                )
                if attempt < max_retries:
                    continue
                break  # exhausted

            break  # success — exit retry loop

        # 4. Visualize (handles both raw_data-present and missing cases; marks completed)
        ret = await viz(scoped())
        token_usage += _apply_subtask_updates(current, ret, sid)

        current["completed"] = True
        return {"subtasks": [current], "token_usage": token_usage}

    return _run


def docs_subtask_runner_node(llm_client: Any, doc_store: Any):
    lookup = docs_lookup_node(doc_store)
    answer = docs_answer_node(llm_client)

    async def _run(state: GraphState) -> dict:
        current: SubtaskResult = dict(state.get("_current_subtask") or {})
        sid = current.get("subtask_id", "?")
        token_usage: list[dict] = []

        def scoped() -> dict:
            return {
                **state,
                "_current_subtask": current,
                "subtasks": [current],
            }

        ret = await lookup(scoped())
        token_usage += _apply_subtask_updates(current, ret, sid)

        ret = await answer(scoped())
        token_usage += _apply_subtask_updates(current, ret, sid)

        current["completed"] = True
        return {"subtasks": [current], "token_usage": token_usage}

    return _run


def lineage_subtask_runner_node(llm_client: Any, lineage_store: Any):
    lookup = lineage_lookup_node(lineage_store)
    answer = lineage_answer_node(llm_client)

    async def _run(state: GraphState) -> dict:
        current: SubtaskResult = dict(state.get("_current_subtask") or {})
        sid = current.get("subtask_id", "?")
        token_usage: list[dict] = []

        def scoped() -> dict:
            return {
                **state,
                "_current_subtask": current,
                "subtasks": [current],
            }

        ret = await lookup(scoped())
        token_usage += _apply_subtask_updates(current, ret, sid)

        ret = await answer(scoped())
        token_usage += _apply_subtask_updates(current, ret, sid)

        current["completed"] = True
        return {"subtasks": [current], "token_usage": token_usage}

    return _run
