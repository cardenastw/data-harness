from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.context.manager import ContextConfig
from app.context.table_docs import TableDocManager
from app.graph.state import GraphState
from app.sql.engine import SQLEngine

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """\
You are a data analyst for Brewed Awakening coffee shops.

RULES:
1. Generate a single SQL query that answers the user's question. Return ONLY the SQL inside a ```sql code fence.
2. NEVER make up data. NEVER guess values.
3. ALWAYS alias every column with a readable name using AS.
4. When the user asks for data "by X", "broken down by X", "per X", or "for each X", your SQL must \
GROUP BY that dimension and return one row per group.
5. Some tables carry `Notes:` describing relationships, required UNIONs/CTEs, test filters, and which \
dimensions don't exist for which tables. Apply them. If the user asks to break data down by a dimension \
that part of the data doesn't have (e.g. location for `cart_orders`), either bucket those rows under a \
clearly labeled synthetic group (e.g. 'Mobile Cart') via UNION ALL, or exclude them with an explicit \
caveat in the response — never invent values and never silently drop rows. When excluding test data, \
check ALL test flags the notes mention (often spread across multiple tables).

IMPORTANT SQLite syntax rules:
 - This is SQLite, NOT PostgreSQL. Do NOT use: DATE_TRUNC, INTERVAL, NOW(), EXTRACT(), ::date casts.
 - Use date('now') for current date. Use strftime() for formatting.
 - Date modifiers MUST be separate arguments: date('now', 'start of month', '-1 month').
 - The ONLY valid modifiers are: 'start of month', 'start of year', 'start of day', '+N days', '-N days', '+N months', '-N months', '+N years', '-N years'. NOTHING ELSE EXISTS.
 - Start of this month: date('now', 'start of month')
 - Start of last month: date('now', 'start of month', '-1 month')
 - To filter by a single month, ALWAYS use a date range: WHERE order_date >= date('now','start of month','-1 month') AND order_date < date('now','start of month')
 - NEVER use strftime to filter: strftime('%Y-%m', order_date) = date(...) is a type mismatch and returns 0 rows.

Common SQLite date mistakes to AVOID:
 - WRONG: date('now', 'start of last month') -> RIGHT: date('now', 'start of month', '-1 month')
 - WRONG: date('now', '-1 month start of month') -> RIGHT: date('now', 'start of month', '-1 month')
 - Each modifier MUST be a separate quoted argument. NEVER combine modifiers in one string.

The data covers January to March 2026.
"""


def context_gatherer_node(
    sql_engine: SQLEngine,
    context_manager: Any,
    table_doc_manager: TableDocManager,
):
    async def _run(state: GraphState) -> dict:
        context_id = state["context_id"]
        context: ContextConfig | None = context_manager.get(context_id)

        if context is None:
            return {"error": f"Unknown context: {context_id}"}

        today = datetime.now().strftime("%Y-%m-%d")
        parts = [BASE_SYSTEM_PROMPT, f"Today's date is {today}."]

        # Context-specific instructions
        if context.system_prompt:
            parts.append(f"Context: {context.name}. {context.system_prompt}")

        # Metric definitions
        if context.metrics:
            defs = "; ".join(f"{m.name}: {m.definition}" for m in context.metrics)
            parts.append(f"Metric definitions: {defs}")

        # Available tables with columns
        table_lines = ["AVAILABLE TABLES AND COLUMNS:"]
        schema_lines = []
        for table_name in context.visible_tables:
            table_doc = table_doc_manager.get(table_name)
            desc = f" — {table_doc.description}" if table_doc and table_doc.description else ""
            notes = table_doc.notes if table_doc else []
            try:
                columns = await sql_engine.get_columns(table_name)
                col_strs = [f"{c.name} ({c.data_type})" for c in columns]
                table_lines.append(f"  - {table_name}{desc}")
                table_lines.append(f"    Columns: {', '.join(col_strs)}")
                schema_lines.append(f"{table_name}: {', '.join(col_strs)}")
            except Exception:
                table_lines.append(f"  - {table_name}{desc}")
                schema_lines.append(f"{table_name}: (schema unavailable)")

            if notes:
                table_lines.append("    Notes:")
                for note in notes:
                    table_lines.append(f"      * {note}")

        parts.append("\n".join(table_lines))
        system_prompt = "\n\n".join(parts)
        schema_text = "\n".join(schema_lines)

        logger.info("Context gathered for '%s' with %d visible tables", context_id, len(context.visible_tables))

        return {
            "system_prompt": system_prompt,
            "schema_text": schema_text,
            "context_config": context,
            "sql_attempts": 0,
        }

    return _run
