from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from numbers import Real
from typing import TYPE_CHECKING, Any, AsyncGenerator, List, Optional

from app.harness.charting import build_auto_chart

if TYPE_CHECKING:
    from app.harness.context_manager import ContextManager
    from app.harness.llm.client import LLMClient
    from app.harness.prompt_builder import PromptBuilder
    from app.harness.sql.engine import SQLEngine
    from app.harness.sql.safety import SQLSafetyValidator
    from app.harness.tool_executor import ToolExecutor
    from app.harness.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_CHART_RECOVERY_ATTEMPTS = 3

CHART_QUERY_SYSTEM_PROMPT = """\
You are a SQL expert. Given a user question, the SQL query that answered it, and \
the database schema, write a single SELECT query that breaks down the answer for \
a chart visualization.

Rules:
- Must return at least 2 rows.
- Must have a label/date column and a numeric column.
- When the answer query filters to a single month, GROUP BY date(column) to get daily rows.
- When the answer query spans multiple months, GROUP BY strftime('%Y-%m', column).
- For non-temporal queries, group by a category column.
- Use the same WHERE filters as the answer query.
- Use SQLite syntax: date('now'), strftime(), etc.
- Date modifiers MUST be separate arguments: date('now', 'start of month', '-1 month').
- Return ONLY the raw SQL query. No explanation, no markdown, no code fences.

Example:
Answer query: SELECT COUNT(*) FROM orders WHERE order_date >= date('now','start of month','-1 month') AND order_date < date('now','start of month')
Chart query: SELECT date(order_date) as day, COUNT(*) as orders FROM orders WHERE order_date >= date('now','start of month','-1 month') AND order_date < date('now','start of month') GROUP BY day ORDER BY day
"""

_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+[\"'`]?(\w+)[\"'`]?", re.IGNORECASE
)


def _strip_sql_from_content(content: str) -> str:
    """Remove SQL code blocks from LLM text output."""
    return re.sub(r"```sql\s*.*?```", "", content, flags=re.DOTALL).strip()


def _extract_sql(text: str) -> str:
    """Extract raw SQL from LLM response, stripping markdown fences if present."""
    # Try to extract from code fences first
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


class Orchestrator:
    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        prompt_builder: PromptBuilder,
        sql_engine: SQLEngine,
        sql_safety_validator: Optional[SQLSafetyValidator] = None,
        settings: Any = None,
        max_iterations: int = 10,
    ):
        self._llm = llm_client
        self._registry = tool_registry
        self._executor = tool_executor
        self._context_manager = context_manager
        self._prompt_builder = prompt_builder
        self._sql_engine = sql_engine
        self._safety = sql_safety_validator
        self._timeout = getattr(settings, "sql_query_timeout", 30.0) if settings else 30.0
        self._max_rows = getattr(settings, "sql_max_rows", 500) if settings else 500
        self._max_iterations = max_iterations

    async def run_stream(
        self, messages: List[dict], context_id: str
    ) -> AsyncGenerator[str, None]:
        context = self._context_manager.get(context_id)
        if context is None:
            yield self._sse("error", {"message": f"Unknown context: {context_id}"})
            return

        system_prompt = await self._prompt_builder.build(context, self._sql_engine)
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        tools = self._registry.to_openai_tools()
        logger.info(f"Tools registered: {[t['function']['name'] for t in tools]}")

        user_question = _last_user_message(messages)

        for iteration in range(self._max_iterations):
            logger.info(f"Orchestrator iteration {iteration + 1}")
            yield self._sse("status", {"message": "Thinking..."})

            response = await self._llm.chat_completion(full_messages, tools=tools)
            choice = response.choices[0]
            message = choice.message

            logger.info(
                f"LLM response - content: {message.content!r}, "
                f"tool_calls: {message.tool_calls}, "
                f"finish_reason: {choice.finish_reason}"
            )

            # If no tool calls, stream the final text
            if not message.tool_calls:
                content = _strip_sql_from_content(message.content or "")
                yield self._sse("content", {"text": content})
                yield self._sse("done", {})
                return

            # Append assistant message with tool calls
            assistant_msg = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            }
            full_messages.append(assistant_msg)

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments

                yield self._sse("status", {"message": _tool_status(tool_name)})
                logger.info(f"Executing tool: {tool_name}")

                result = await self._executor.execute(tool_name, tool_args, context)

                # Build tool response for LLM
                if result.error:
                    tool_response = json.dumps({"error": result.error})
                else:
                    tool_response = json.dumps(result.data)

                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_response,
                })

                # When run_sql returns answer data, generate a chart separately
                if result.artifact_type == "sql" and result.data and not result.error:
                    answer = result.data
                    answer_query = answer.get("query", "")

                    yield self._sse("status", {"message": "Preparing chart..."})
                    chart = await self._build_chart_for_answer(
                        user_question, answer_query, answer, context,
                    )

                    yield self._sse("artifact", {
                        "type": "sql",
                        "query": answer_query,
                        "result": answer,
                    })

                    if chart:
                        yield self._sse("artifact", {
                            "type": "chart",
                            "config": chart,
                        })
                        yield self._sse("content", {
                            "text": _format_artifact_summary(answer, chart),
                        })
                    else:
                        yield self._sse("content", {
                            "text": _format_answer_only_summary(answer),
                        })

                    yield self._sse("done", {})
                    return

                elif result.error:
                    yield self._sse("status", {"message": "Checking the data..."})

        yield self._sse("content", {"text": "I couldn't complete that request."})
        yield self._sse("done", {})

    # ------------------------------------------------------------------
    # Chart generation (separate LLM call)
    # ------------------------------------------------------------------

    async def _build_chart_for_answer(
        self,
        user_question: str,
        answer_query: str,
        answer: dict,
        context: Any,
    ) -> Optional[dict]:
        """Generate, execute, and validate a chart query. Returns chart config or None."""
        schema_text = await self._get_schema_for_query(answer_query, context)
        last_failed_sql: Optional[str] = None
        last_error: Optional[str] = None

        for attempt in range(MAX_CHART_RECOVERY_ATTEMPTS):
            chart_sql = await self._generate_chart_query(
                user_question, answer_query, context, schema_text,
                failed_sql=last_failed_sql,
                failed_reason=last_error,
            )
            if not chart_sql:
                logger.warning("Chart LLM returned empty SQL")
                last_error = "LLM returned empty response"
                continue

            logger.info(f"Chart SQL (attempt {attempt + 1}): {chart_sql}")

            chart_result = await self._execute_chart_query(chart_sql, context)
            if chart_result is None:
                last_failed_sql = chart_sql
                last_error = "Query execution failed"
                continue

            chart_build = build_auto_chart(
                chart_result["columns"],
                chart_result["rows"],
                context.chart_preferences,
            )
            if chart_build.error:
                logger.info(f"Chart validation failed (attempt {attempt + 1}): {chart_build.error}")
                last_failed_sql = chart_sql
                last_error = chart_build.error
                continue

            chart = chart_build.chart
            total_err = _chart_total_error(answer, chart)
            if total_err:
                logger.info(f"Chart total mismatch (attempt {attempt + 1}): {total_err}")
                last_failed_sql = chart_sql
                last_error = total_err
                continue

            return chart

        logger.info("Chart generation exhausted retries, falling back to answer-only")
        return None

    async def _get_schema_for_query(self, query: str, context: Any) -> str:
        """Fetch column info for tables referenced in the query."""
        referenced = set(_TABLE_PATTERN.findall(query))
        visible = set(context.visible_tables)
        tables = referenced & visible
        if not tables:
            tables = visible

        lines = []
        for table_name in sorted(tables):
            try:
                columns = await self._sql_engine.get_columns(table_name)
                col_strs = [f"{c.name} ({c.data_type})" for c in columns]
                lines.append(f"{table_name}: {', '.join(col_strs)}")
            except Exception:
                lines.append(f"{table_name}: (schema unavailable)")
        return "\n".join(lines)

    async def _generate_chart_query(
        self,
        user_question: str,
        answer_query: str,
        context: Any,
        schema_text: str,
        failed_sql: Optional[str] = None,
        failed_reason: Optional[str] = None,
    ) -> Optional[str]:
        """Ask the LLM (separate call) to produce a chart SQL query."""
        today = datetime.now().strftime("%Y-%m-%d")

        user_content = (
            f"User question: {user_question}\n"
            f"Answer query: {answer_query}\n"
            f"Today's date: {today}\n\n"
            f"Schema:\n{schema_text}"
        )
        if failed_sql and failed_reason:
            user_content += (
                f"\n\nYour previous query was rejected:\n"
                f"  SQL: {failed_sql}\n"
                f"  Error: {failed_reason}\n"
                f"Write a different query that fixes this error."
            )

        try:
            response = await self._llm.chat_completion([
                {"role": "system", "content": CHART_QUERY_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ])
            raw = response.choices[0].message.content or ""
            return _extract_sql(raw) or None
        except Exception:
            logger.exception("Chart query generation LLM call failed")
            return None

    async def _execute_chart_query(
        self,
        sql: str,
        context: Any,
    ) -> Optional[dict]:
        """Validate and execute a chart SQL query directly."""
        if self._safety:
            validation = self._safety.validate(sql)
            if not validation.is_safe:
                logger.warning(f"Chart query rejected by safety: {validation.reason}")
                return None

        referenced_tables = set(_TABLE_PATTERN.findall(sql))
        visible = set(context.visible_tables)
        unauthorized = referenced_tables - visible
        if unauthorized:
            logger.warning(f"Chart query references unauthorized tables: {unauthorized}")
            return None

        try:
            result = await self._sql_engine.execute_query(
                sql,
                timeout_seconds=self._timeout,
                max_rows=self._max_rows,
            )
        except Exception:
            logger.exception("Chart query execution failed")
            return None

        return {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
        }

    def _sse(self, event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _last_user_message(messages: List[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


def _tool_status(tool_name: str) -> str:
    if tool_name == "get_schema":
        return "Inspecting data..."
    if tool_name == "run_sql":
        return "Analyzing data..."
    return "Working..."


def _format_artifact_summary(answer: dict, chart: dict) -> str:
    return "\n\n".join([
        _format_answer_sentence(answer),
        _format_chart_sentence(chart),
        "\n".join(_follow_up_lines(answer, chart)),
    ])


def _format_answer_only_summary(answer: dict | None) -> str:
    if not answer:
        return "I couldn't complete that request."

    return "\n\n".join([
        _format_answer_sentence(answer),
        "I couldn't prepare a chart for this answer.",
        "\n".join([
            "Suggested follow-ups:",
            "- Compare this with the prior period.",
            "- Break this down by location.",
            "- Show revenue for the same period.",
        ]),
    ])


def _format_answer_sentence(answer: dict) -> str:
    columns = answer.get("columns", [])
    rows = answer.get("rows", [])
    if len(rows) == 1 and len(rows[0]) == 1 and columns:
        label = _humanize_label(columns[0])
        return f"{label}: {_format_value(rows[0][0])}."

    row_count = answer.get("row_count", len(rows))
    return f"Returned {row_count} row{'' if row_count == 1 else 's'}."


def _format_chart_sentence(chart: dict) -> str:
    chart_type = str(chart.get("chartType", "chart"))
    x_label = _humanize_label(str(chart.get("xLabel") or chart.get("xAxis") or ""))
    y_label = _humanize_label(str(chart.get("yLabel") or chart.get("yAxis") or ""))

    if x_label and y_label:
        return f"{y_label} by {x_label} is included as a {chart_type} chart."
    return f"A {chart_type} chart is included."


def _follow_up_lines(answer: dict, chart: dict) -> list[str]:
    metric = _metric_label(answer, chart).lower()
    secondary_metric = "orders" if "revenue" in metric else "revenue"

    return [
        "Suggested follow-ups:",
        f"- Compare {metric} with the prior period.",
        f"- Break {metric} down by location.",
        f"- Show {secondary_metric} for the same period.",
    ]


def _metric_label(answer: dict, chart: dict) -> str:
    chart_metric = str(chart.get("yLabel") or chart.get("yAxis") or "").strip()
    if chart_metric:
        return _humanize_label(chart_metric)

    columns = answer.get("columns", [])
    if columns:
        return _humanize_label(str(columns[0]))

    return "this metric"


def _chart_total_error(answer: dict | None, chart: dict) -> str | None:
    answer_value = _single_answer_number(answer)
    if answer_value is None:
        return None

    y_axis = chart.get("yAxis")
    if not isinstance(y_axis, str) or not _is_additive_metric_name(y_axis):
        return None

    chart_values = [
        value
        for point in chart.get("data", [])
        if isinstance(point, dict)
        for value in [_coerce_number(point.get(y_axis))]
        if value is not None
    ]
    if len(chart_values) < 2:
        return None

    chart_total = sum(chart_values)
    tolerance = max(0.01, abs(answer_value) * 0.001)
    if abs(chart_total - answer_value) <= tolerance:
        return None

    return (
        f"chart values sum to {_format_value(chart_total)}, "
        f"but the answer is {_format_value(answer_value)}"
    )


def _single_answer_number(answer: dict | None) -> float | None:
    if not answer:
        return None

    rows = answer.get("rows", [])
    if len(rows) != 1 or len(rows[0]) != 1:
        return None
    return _coerce_number(rows[0][0])


def _is_additive_metric_name(name: str) -> bool:
    lowered = name.lower()
    if any(
        keyword in lowered
        for keyword in ("avg", "average", "rate", "ratio", "percent", "margin")
    ):
        return False

    return any(
        keyword in lowered
        for keyword in (
            "count",
            "total",
            "sum",
            "orders",
            "revenue",
            "sales",
            "members",
            "customers",
            "quantity",
            "sold",
        )
    )


def _coerce_number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None

    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None

    return None


def _format_value(value) -> str:
    number = _coerce_number(value)
    if number is None:
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _humanize_label(label: str) -> str:
    return label.replace("_", " ").strip().title()
