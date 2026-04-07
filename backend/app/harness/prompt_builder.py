from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from app.harness.context_manager import ContextConfig
from app.harness.sql.engine import SQLEngine
from app.harness.table_docs import TableDocManager

BASE_SYSTEM_PROMPT = """\
You are a data analyst for Brewed Awakening coffee shops.

RULES:
1. First use get_schema to inspect the tables you need, then use run_sql. NEVER write SQL in your text response.
2. NEVER make up data. Only report data returned by run_sql.
3. Keep text responses brief. Summarize what the data shows.
4. ALWAYS end your response with 2-3 suggested follow-up questions.
5. In final user-facing text, never mention tool names, SQL query planning, retries, or internal errors.

The run_sql tool takes a "query" that directly answers the user's question (e.g. a count, a sum, a list).

IMPORTANT SQLite syntax rules:
 - Use date('now') not NOW(). Use strftime() for formatting.
 - Date modifiers MUST be separate arguments: date('now', 'start of month', '-1 month') NOT date('now', 'start of month - 1 month').
 - Last month: WHERE order_date >= date('now', 'start of month', '-1 month') AND order_date < date('now', 'start of month')
 - This month: WHERE order_date >= date('now', 'start of month')
- The data covers January to March 2026.
"""


class PromptBuilder:
    def __init__(self, table_doc_manager: Optional[TableDocManager] = None):
        self._table_docs = table_doc_manager

    async def build(self, context: ContextConfig, sql_engine: SQLEngine) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        parts = [BASE_SYSTEM_PROMPT, f"Today's date is {today}."]

        # Context-specific instructions
        if context.system_prompt:
            parts.append(f"Context: {context.name}. {context.system_prompt}")

        # Metric definitions
        if context.metrics:
            defs = "; ".join(f"{m.name}: {m.definition}" for m in context.metrics)
            parts.append(f"Metric definitions: {defs}")

        # List available tables (lightweight — use get_schema for full details)
        table_lines = ["AVAILABLE TABLES (use get_schema to inspect columns before writing SQL):"]
        for table_name in context.visible_tables:
            table_doc = self._table_docs.get(table_name) if self._table_docs else None
            if table_doc and table_doc.description:
                table_lines.append(f"  - {table_name} — {table_doc.description}")
            else:
                table_lines.append(f"  - {table_name}")
        parts.append("\n".join(table_lines))

        return "\n\n".join(parts)
