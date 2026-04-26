from __future__ import annotations

import logging

from app.graph.state import GraphState
from app.sql.sqlite_engine import SQLiteEngine

logger = logging.getLogger(__name__)


def executor_node(sql_engine: SQLiteEngine, timeout: float = 30.0, max_rows: int = 500):
    async def _run(state: GraphState) -> dict:
        current = state.get("_current_subtask") or {}
        subtask_id = current.get("subtask_id", "?")
        sql = ""
        for st in state.get("subtasks", []) or []:
            if st.get("subtask_id") == subtask_id:
                sql = st.get("generated_sql", "")
                break
        if not sql:
            sql = current.get("generated_sql", "")

        try:
            result = await sql_engine.execute_query(
                sql, timeout_seconds=timeout, max_rows=max_rows,
            )
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("SQL execution failed [%s]: %s", subtask_id, error_msg)
            return {
                "subtasks": [
                    {
                        "subtask_id": subtask_id,
                        "execution_error": error_msg,
                        "raw_data": None,
                    }
                ]
            }

        data = {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "execution_time_ms": result.execution_time_ms,
        }

        logger.info(
            "SQL executed [%s]: %d rows in %.1fms",
            subtask_id,
            result.row_count,
            result.execution_time_ms,
        )

        return {
            "subtasks": [
                {
                    "subtask_id": subtask_id,
                    "raw_data": data,
                    "execution_error": None,
                }
            ]
        }

    return _run
