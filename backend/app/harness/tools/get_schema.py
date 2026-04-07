from __future__ import annotations

from typing import Any, Optional

from app.harness.sql.engine import SQLEngine
from app.harness.table_docs import TableDocManager

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

    def __init__(self, sql_engine: SQLEngine, table_doc_manager: Optional[TableDocManager] = None):
        self._engine = sql_engine
        self._table_docs = table_doc_manager

    async def execute(self, arguments: dict, context: Any) -> ToolResult:
        visible_tables = context.visible_tables
        table_name = arguments.get("table_name")

        if table_name:
            if table_name not in visible_tables:
                return ToolResult(error=f"Table '{table_name}' is not available in this context")
            columns = await self._engine.get_columns(table_name)
            table_doc = self._table_docs.get(table_name) if self._table_docs else None

            col_info = []
            for c in columns:
                col = {
                    "name": c.name,
                    "type": c.data_type,
                    "nullable": c.nullable,
                    "primary_key": c.is_primary_key,
                }
                if table_doc and c.name in table_doc.columns:
                    col_doc = table_doc.columns[c.name]
                    if col_doc.values:
                        col["values"] = col_doc.values
                    if col_doc.description:
                        col["description"] = col_doc.description
                col_info.append(col)

            schema_info = {"table": table_name, "columns": col_info}
            if table_doc and table_doc.description:
                schema_info["description"] = table_doc.description
            return ToolResult(data=schema_info)

        # Return all visible tables with columns (lightweight, no docs)
        all_tables = await self._engine.get_tables()
        schema_info = []
        for table in all_tables:
            if table.name not in visible_tables:
                continue
            columns = await self._engine.get_columns(table.name)
            entry = {
                "table": table.name,
                "row_count": table.row_count,
                "columns": [{"name": c.name, "type": c.data_type} for c in columns],
            }
            table_doc = self._table_docs.get(table.name) if self._table_docs else None
            if table_doc and table_doc.description:
                entry["description"] = table_doc.description
            schema_info.append(entry)
        return ToolResult(data=schema_info)
