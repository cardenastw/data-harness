from __future__ import annotations

import re
from typing import Any

from app.harness.sql.engine import SQLEngine
from app.harness.sql.safety import SQLSafetyValidator

from .base import BaseTool, ToolResult


class RunSQLTool(BaseTool):
    name = "run_sql"
    description = (
        "Execute a read-only SQL query against the database. Only SELECT statements are allowed. "
        "Always use get_schema first to understand table structure before writing queries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The SQL SELECT query to execute. Must be read-only.",
            }
        },
        "required": ["query"],
    }

    # Pattern to extract table names from SQL (simplified)
    _TABLE_PATTERN = re.compile(
        r"\b(?:FROM|JOIN)\s+[\"']?(\w+)[\"']?", re.IGNORECASE
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

        # Safety validation
        validation = self._safety.validate(query)
        if not validation.is_safe:
            return ToolResult(error=f"Query rejected: {validation.reason}")

        # Table access validation
        referenced_tables = set(self._TABLE_PATTERN.findall(query))
        visible = set(context.visible_tables)
        unauthorized = referenced_tables - visible
        if unauthorized:
            return ToolResult(
                error=f"Access denied to tables: {', '.join(sorted(unauthorized))}"
            )

        # Execute
        try:
            result = await self._engine.execute_query(
                query,
                timeout_seconds=self._timeout,
                max_rows=self._max_rows,
            )
        except TimeoutError:
            return ToolResult(error=f"Query timed out after {self._timeout}s")
        except Exception as e:
            return ToolResult(error=f"Query error: {e}")

        return ToolResult(
            data={
                "columns": result.columns,
                "rows": result.rows,
                "row_count": result.row_count,
                "truncated": result.truncated,
                "execution_time_ms": result.execution_time_ms,
            },
            artifact_type="sql",
        )
