from __future__ import annotations

from typing import Any, List, Optional

from app.harness.context_manager import ContextConfig
from app.harness.sql.engine import SQLEngine

BASE_SYSTEM_PROMPT = """\
You are a data analyst for Brewed Awakening coffee shops.

RULES:
1. Use the run_sql tool to execute SQL queries. NEVER write SQL in your text.
2. After getting results, call create_chart to visualize the data.
3. NEVER make up data. Only report data returned by run_sql.
4. Keep text responses brief.
5. ALWAYS end your response with 2-3 suggested follow-up questions the user could explore next.

WORKFLOW: call run_sql -> call create_chart -> summarize results -> suggest next questions.
"""


class PromptBuilder:
    async def build(self, context: ContextConfig, sql_engine: SQLEngine) -> str:
        parts = [BASE_SYSTEM_PROMPT]

        # Context-specific instructions
        if context.system_prompt:
            parts.append(f"Context: {context.name}. {context.system_prompt}")

        # Metric definitions
        if context.metrics:
            defs = "; ".join(f"{m.name}: {m.definition}" for m in context.metrics)
            parts.append(f"Metric definitions: {defs}")

        # Pre-load schema into prompt so the model doesn't need to discover it
        schema_lines = ["DATABASE SCHEMA (use these exact table and column names):"]
        for table_name in context.visible_tables:
            columns = await sql_engine.get_columns(table_name)
            col_defs = ", ".join(f"{c.name} ({c.data_type})" for c in columns)
            schema_lines.append(f"  {table_name}: {col_defs}")
        parts.append("\n".join(schema_lines))

        return "\n\n".join(parts)
