from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from app.charting import build_auto_chart
from app.graph.state import GraphState
from app.sql.safety import SQLSafetyValidator
from app.sql.sqlite_engine import SQLiteEngine

logger = logging.getLogger(__name__)

MAX_CHART_ATTEMPTS = 3

CHART_QUERY_SYSTEM_PROMPT = """\
You are a SQL and data visualization expert. Given a user question, the SQL query \
that answered it, and the database schema, produce a chart specification.

Return a JSON object with these fields:
- "query": a single SELECT query that breaks down the answer for charting (must return at least 2 rows)
- "chart_type": one of "bar", "line", "pie", "area", "scatter"
- "title": a short human-readable chart title

Pick chart_type based on the data shape:
- "line" for time series (data over days/months/years)
- "bar" for comparing categories (locations, products, statuses)
- "pie" for showing composition/share (small number of categories, max 6-8 slices)
- "area" for cumulative or stacked time series
- "scatter" for showing correlation between two numeric values

SQL rules:
- Must have a label/date column and a numeric column.
- When the answer query filters to a single month, GROUP BY date(column) to get daily rows.
- When the answer query spans multiple months, GROUP BY strftime('%Y-%m', column).
- For non-temporal queries, group by a category column.
- Use SQLite syntax: date('now'), strftime(), etc.
- Date modifiers MUST be separate arguments: date('now', 'start of month', '-1 month').
- The ONLY valid modifiers are: 'start of month', 'start of year', 'start of day', '+N days', '-N days', '+N months', '-N months', '+N years', '-N years'. NOTHING ELSE EXISTS.

Return ONLY the JSON object. No explanation, no markdown, no code fences.

Example:
{"query": "SELECT date(order_date) as day, COUNT(*) as orders FROM orders WHERE order_date >= date('now','start of month','-1 month') AND order_date < date('now','start of month') GROUP BY day ORDER BY day", "chart_type": "line", "title": "Daily Orders Last Month"}
"""

_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+[\"'`]?(\w+)[\"'`]?", re.IGNORECASE
)


def _extract_usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def _parse_chart_response(raw: str) -> Optional[dict]:
    text = raw.strip()
    try:
        if text.startswith("```"):
            match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("query"):
            return {
                "query": parsed["query"].replace(";", ""),
                "chart_type": parsed.get("chart_type"),
                "title": parsed.get("title"),
            }
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def visualization_node(
    llm_client: Any,
    sql_engine: SQLiteEngine,
    safety: SQLSafetyValidator,
    timeout: float = 30.0,
    max_rows: int = 500,
):
    async def _run(state: GraphState) -> dict:
        raw_data = state.get("raw_data")
        if not raw_data:
            return {"chart_json": None, "token_usage": []}

        user_question = state["user_question"]
        answer_query = state.get("generated_sql", "")
        schema_text = state.get("schema_text", "")
        context = state.get("context_config")
        today = datetime.now().strftime("%Y-%m-%d")

        last_failed_sql: Optional[str] = None
        last_error: Optional[str] = None
        usage_entries: list[dict] = []

        for attempt in range(MAX_CHART_ATTEMPTS):
            # Ask LLM for chart spec
            user_content = (
                f"User question: {user_question}\n"
                f"Answer query: {answer_query}\n"
                f"Today's date: {today}\n\n"
                f"Schema:\n{schema_text}"
            )
            if last_failed_sql and last_error:
                user_content += (
                    f"\n\nYour previous chart spec was rejected:\n"
                    f"  SQL: {last_failed_sql}\n"
                    f"  Error: {last_error}\n"
                    f"Write a different query that fixes this error."
                )

            try:
                response = await llm_client.chat_completion([
                    {"role": "system", "content": CHART_QUERY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ])
                usage_entries.append(_extract_usage(response))
                raw = response.choices[0].message.content or ""
                chart_spec = _parse_chart_response(raw)
            except Exception:
                logger.exception("Chart LLM call failed (attempt %d)", attempt + 1)
                last_error = "LLM call failed"
                continue

            if not chart_spec:
                last_error = "LLM returned empty/unparseable response"
                continue

            chart_sql = chart_spec["query"]
            logger.info("Chart SQL (attempt %d): %s", attempt + 1, chart_sql[:200])

            # Validate chart SQL
            validation = safety.validate(chart_sql)
            if not validation.is_safe:
                last_failed_sql = chart_sql
                last_error = validation.reason
                continue

            # Table access check
            if context and hasattr(context, "visible_tables"):
                referenced = set(_TABLE_PATTERN.findall(chart_sql))
                visible = set(context.visible_tables)
                if referenced - visible:
                    last_failed_sql = chart_sql
                    last_error = f"References unauthorized tables: {referenced - visible}"
                    continue

            # Execute chart query
            try:
                result = await sql_engine.execute_query(
                    chart_sql, timeout_seconds=timeout, max_rows=max_rows,
                )
            except Exception as exc:
                last_failed_sql = chart_sql
                last_error = str(exc)
                continue

            # Build chart
            chart_build = build_auto_chart(
                result.columns,
                result.rows,
                context.chart_preferences if context else None,
                chart_type=chart_spec.get("chart_type"),
                title=chart_spec.get("title"),
            )

            if chart_build.error:
                last_failed_sql = chart_sql
                last_error = chart_build.error
                continue

            logger.info("Chart generated successfully on attempt %d", attempt + 1)
            return {"chart_json": chart_build.chart, "token_usage": usage_entries}

        logger.info("Chart generation exhausted %d attempts", MAX_CHART_ATTEMPTS)
        return {"chart_json": None, "token_usage": usage_entries}

    return _run
