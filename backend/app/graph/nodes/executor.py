from __future__ import annotations

import logging
from typing import Any

from app.graph.state import GraphState
from app.sql.sqlite_engine import SQLiteEngine

logger = logging.getLogger(__name__)


def executor_node(sql_engine: SQLiteEngine, timeout: float = 30.0, max_rows: int = 500):
    async def _run(state: GraphState) -> dict:
        sql = state.get("generated_sql", "")

        try:
            result = await sql_engine.execute_query(
                sql, timeout_seconds=timeout, max_rows=max_rows,
            )
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("SQL execution failed: %s", error_msg)
            return {"execution_error": error_msg, "raw_data": None}

        data = {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "execution_time_ms": result.execution_time_ms,
        }

        logger.info(
            "SQL executed: %d rows in %.1fms",
            result.row_count,
            result.execution_time_ms,
        )

        return {"raw_data": data, "execution_error": None}

    return _run
