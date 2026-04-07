from __future__ import annotations

from typing import Any

from app.harness.sql.engine import SQLEngine

from .base import BaseTool, ToolResult


class GetSchemaTool(BaseTool):
    name = "get_schema"
    description = (
        "Get the database schema showing available tables and their columns. "
        "Use this to understand what data is available before writing SQL queries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "Optional: specific table name to get detailed schema for. If omitted, returns all available tables.",
            }
        },
        "required": [],
    }

    def __init__(self, sql_engine: SQLEngine):
        self._engine = sql_engine

    async def execute(self, arguments: dict, context: Any) -> ToolResult:
        visible_tables = context.visible_tables
        table_name = arguments.get("table_name")

        if table_name:
            if table_name not in visible_tables:
                return ToolResult(error=f"Table '{table_name}' is not available in this context")
            columns = await self._engine.get_columns(table_name)
            schema_info = {
                "table": table_name,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.data_type,
                        "nullable": c.nullable,
                        "primary_key": c.is_primary_key,
                    }
                    for c in columns
                ],
            }
            return ToolResult(data=schema_info)

        # Return all visible tables with columns
        all_tables = await self._engine.get_tables()
        schema_info = []
        for table in all_tables:
            if table.name not in visible_tables:
                continue
            columns = await self._engine.get_columns(table.name)
            schema_info.append({
                "table": table.name,
                "row_count": table.row_count,
                "columns": [
                    {"name": c.name, "type": c.data_type}
                    for c in columns
                ],
            })
        return ToolResult(data=schema_info)
