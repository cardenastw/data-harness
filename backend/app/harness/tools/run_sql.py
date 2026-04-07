from __future__ import annotations

import re
from typing import Any

from app.harness.sql.engine import SQLEngine
from app.harness.sql.safety import SQLSafetyValidator

from .base import BaseTool, ToolResult


class RunSQLTool(BaseTool):
    name = "run_sql"
    description = (
        "Execute a SQL SELECT query to answer the user's question. "
        "Must be a SELECT statement using SQLite syntax."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL query that directly answers the user's question.",
            },
        },
        "required": ["query"],
    }

    _TABLE_PATTERN = re.compile(
        r"\b(?:FROM|JOIN)\s+[\"'`]?(\w+)[\"'`]?", re.IGNORECASE
    )

    def __init__(self, sql_engine: SQLEngine, safety_validator: SQLSafetyValidator, settings: Any):
        self._engine = sql_engine
        self._safety = safety_validator
        self._timeout = settings.sql_query_timeout
        self._max_rows = settings.sql_max_rows

    async def execute(self, arguments: dict, context: Any) -> ToolResult:
        query = arguments.get("query", "").strip()

        if not query:
            return ToolResult(error="No query provided")

        result = await self._run_query(query, context)
        if result.get("error"):
            return ToolResult(error=result["error"])

        return ToolResult(
            data={**result, "query": query},
            artifact_type="sql",
        )

    async def _run_query(self, query: str, context: Any) -> dict:
        validation = self._safety.validate(query)
        if not validation.is_safe:
            return {"error": f"Query rejected: {validation.reason}"}

        referenced_tables = set(self._TABLE_PATTERN.findall(query))
        visible = set(context.visible_tables)
        unauthorized = referenced_tables - visible
        if unauthorized:
            return {"error": f"Access denied to tables: {', '.join(sorted(unauthorized))}"}

        try:
            result = await self._engine.execute_query(
                query,
                timeout_seconds=self._timeout,
                max_rows=self._max_rows,
            )
        except TimeoutError:
            return {"error": f"Query timed out after {self._timeout}s"}
        except Exception as e:
            return {"error": f"Query error: {e}"}

        return {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "execution_time_ms": result.execution_time_ms,
        }
