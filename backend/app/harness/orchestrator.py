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
MAX_TEXT_RETRIES = 2   # LLM responds without calling any tool
MAX_SQL_ERRORS = 3     # run_sql returns an error

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
- Use the same WHERE filters as the answer query.
- Use SQLite syntax: date('now'), strftime(), etc.
- Date modifiers MUST be separate arguments: date('now', 'start of month', '-1 month').
- The ONLY valid modifiers are: 'start of month', 'start of year', 'start of day', '+N days', '-N days', '+N months', '-N months', '+N years', '-N years'. NOTHING ELSE EXISTS.

Common mistakes to AVOID:
- WRONG: date('now', 'start of last month') → RIGHT: date('now', 'start of month', '-1 month')
- WRONG: date('now', '-1 month start of month') → RIGHT: date('now', 'start of month', '-1 month')
- Each modifier MUST be a separate quoted argument.

Return ONLY the JSON object. No explanation, no markdown, no code fences.

Example:
{"query": "SELECT date(order_date) as day, COUNT(*) as orders FROM orders WHERE order_date >= date('now','start of month','-1 month') AND order_date < date('now','start of month') GROUP BY day ORDER BY day", "chart_type": "line", "title": "Daily Orders Last Month"}
"""

_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+[\"'`]?(\w+)[\"'`]?", re.IGNORECASE
)


def _parse_chart_response(raw: str) -> Optional[dict]:
    """Parse the chart LLM response as JSON, falling back to raw SQL extraction."""
    text = raw.strip()
    # Try JSON first
    try:
        # Strip markdown fences if present
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

    # Fallback: treat as raw SQL
    sql = _extract_sql(text)
    if sql:
        return {"query": sql, "chart_type": None, "title": None}
    return None


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
        self, messages: List[dict], context_id: str,
        turn_messages: Optional[List[dict]] = None,
        query_state_out: Optional[list] = None,
        prior_query_state: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        context = self._context_manager.get(context_id)
        if context is None:
            yield self._sse("error", {"message": f"Unknown context: {context_id}"})
            return

        system_prompt = await self._prompt_builder.build(context, self._sql_engine)
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        # Inject structured state from the prior turn's query
        if prior_query_state:
            state_text = _format_query_state(prior_query_state)
            for i in range(len(full_messages) - 1, -1, -1):
                if full_messages[i].get("role") == "user":
                    full_messages[i] = {
                        **full_messages[i],
                        "content": full_messages[i]["content"] + "\n\n" + state_text,
                    }
                    break

        tools = self._registry.to_openai_tools()
        logger.info(f"Tools registered: {[t['function']['name'] for t in tools]}")

        user_question = _last_user_message(messages)
        has_called_run_sql = False
        text_retries = 0
        sql_errors = 0
        attempted_sql: set = set()

        for iteration in range(self._max_iterations):
            logger.info(f"Orchestrator iteration {iteration + 1}")
            yield self._sse("status", {"message": "Thinking..."})

            # Force tool use if the LLM hasn't queried data yet and SQL retries remain
            tool_choice = (
                "required"
                if not has_called_run_sql and iteration > 0 and sql_errors < MAX_SQL_ERRORS
                else None
            )
            response = await self._llm.chat_completion(
                full_messages, tools=tools, tool_choice=tool_choice,
            )
            choice = response.choices[0]
            message = choice.message

            logger.info(
                f"LLM response - content: {message.content!r}, "
                f"tool_calls: {message.tool_calls}, "
                f"finish_reason: {choice.finish_reason}"
            )

            if not message.tool_calls:
                if not has_called_run_sql:
                    text_retries += 1
                    if text_retries <= MAX_TEXT_RETRIES and sql_errors < MAX_SQL_ERRORS:
                        logger.info("LLM responded without calling run_sql, retrying with forced tool use")
                        if message.content:
                            full_messages.append({"role": "assistant", "content": message.content})
                            full_messages.append({
                                "role": "user",
                                "content": "You must call run_sql to answer this question. Do not answer from memory.",
                            })
                        continue
                    logger.info(
                        "Retry budget exhausted (text_retries=%d, sql_errors=%d), returning error",
                        text_retries, sql_errors,
                    )
                    yield self._sse("content", {
                        "text": "I wasn't able to retrieve that data. Could you try rephrasing your question?",
                    })
                    yield self._sse("done", {})
                    return

                content = _strip_sql_from_content(message.content or "")
                if turn_messages is not None:
                    turn_messages.append({"role": "assistant", "content": content})
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
            if turn_messages is not None:
                turn_messages.append(assistant_msg)

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments

                # Dedup guard: skip if the LLM sends the same run_sql query again
                if tool_name == "run_sql":
                    try:
                        parsed_args = json.loads(tool_args)
                    except json.JSONDecodeError:
                        parsed_args = {}
                    normalized = _normalize_sql(parsed_args.get("query", ""))
                    if normalized in attempted_sql:
                        logger.info("Duplicate SQL query detected, skipping execution")
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({
                                "error": "You already tried this exact query and it failed. Write a DIFFERENT query."
                            }),
                        }
                        full_messages.append(tool_msg)
                        sql_errors += 1
                        continue
                    attempted_sql.add(normalized)

                yield self._sse("status", {"message": _tool_status(tool_name)})
                logger.info(f"Executing tool: {tool_name}")

                result = await self._executor.execute(tool_name, tool_args, context)

                # Build tool response for LLM
                if result.error:
                    tool_response = json.dumps({"error": result.error})
                else:
                    tool_response = json.dumps(result.data)

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_response,
                }
                full_messages.append(tool_msg)
                if turn_messages is not None:
                    turn_messages.append(tool_msg)

                # When run_sql returns answer data, generate a chart separately
                if result.artifact_type == "sql" and result.data and not result.error:
                    has_called_run_sql = True
                    answer = result.data
                    answer_query = answer.get("query", "")

                    if query_state_out is not None:
                        query_state_out.append(_build_query_state(answer_query, answer))

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
                        content_text = _format_artifact_summary(answer, chart, user_question)
                    else:
                        content_text = _format_answer_only_summary(answer, user_question)

                    if turn_messages is not None:
                        turn_messages.append({"role": "assistant", "content": content_text})
                    yield self._sse("content", {"text": content_text})

                    yield self._sse("done", {})
                    return

                elif result.error:
                    sql_errors += 1
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
        attempted_chart_sql: set = set()

        for attempt in range(MAX_CHART_RECOVERY_ATTEMPTS):
            chart_spec = await self._generate_chart_query(
                user_question, answer_query, context, schema_text,
                failed_sql=last_failed_sql,
                failed_reason=last_error,
            )
            if not chart_spec:
                logger.warning("Chart LLM returned empty response")
                last_error = "LLM returned empty response"
                continue

            chart_sql = chart_spec["query"]
            normalized = _normalize_sql(chart_sql)
            if normalized in attempted_chart_sql:
                logger.info(f"Chart SQL (attempt {attempt + 1}): duplicate query, skipping")
                last_failed_sql = chart_sql
                last_error = "You generated the same query as a previous attempt. Write a different query."
                continue
            attempted_chart_sql.add(normalized)

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
                chart_type=chart_spec.get("chart_type"),
                title=chart_spec.get("title"),
            )
            if chart_build.error:
                logger.info(f"Chart validation failed (attempt {attempt + 1}): {chart_build.error}")
                last_failed_sql = chart_sql
                last_error = chart_build.error
                continue

            return chart_build.chart

        logger.info("Chart generation exhausted retries, falling back to answer-only")
        return None

    async def _get_schema_for_query(self, query: str, context: Any) -> str:
        """Fetch column info for all visible tables so the chart LLM can JOIN as needed."""
        lines = []
        for table_name in sorted(context.visible_tables):
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
    ) -> Optional[dict]:
        """Ask the LLM to produce a chart spec (SQL + chart_type + title)."""
        today = datetime.now().strftime("%Y-%m-%d")

        user_content = (
            f"User question: {user_question}\n"
            f"Answer query: {answer_query}\n"
            f"Today's date: {today}\n\n"
            f"Schema:\n{schema_text}"
        )
        if failed_sql and failed_reason:
            user_content += (
                f"\n\nYour previous chart spec was rejected:\n"
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
            return _parse_chart_response(raw)
        except Exception:
            logger.exception("Chart query generation LLM call failed")
            return None

    async def _execute_chart_query(
        self,
        sql: str,
        context: Any,
    ) -> Optional[dict]:
        """Validate and execute a chart SQL query directly."""
        sql = sql.replace(";", "")

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

def _normalize_sql(sql: str) -> str:
    """Normalize SQL for dedup comparison."""
    return " ".join(sql.lower().split())


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


_BREAKDOWN_PATTERN = re.compile(
    r"\b(?:by|per|for each|broken down by|grouped by)\s+(\w+)",
    re.IGNORECASE,
)


def _dimensions_in_question(question: str) -> set[str]:
    """Return lowercase dimension keywords the user already asked to break down by."""
    return {m.group(1).lower() for m in _BREAKDOWN_PATTERN.finditer(question)}


def _format_artifact_summary(answer: dict, chart: dict, user_question: str = "") -> str:
    return "\n\n".join([
        _format_answer_sentence(answer),
        _format_chart_sentence(chart),
        "\n".join(_follow_up_lines(answer, chart, user_question)),
    ])


def _format_answer_only_summary(answer: dict | None, user_question: str = "") -> str:
    if not answer:
        return "I couldn't complete that request."

    return "\n\n".join([
        _format_answer_sentence(answer),
        "I couldn't prepare a chart for this answer.",
        "\n".join(_answer_only_follow_ups(user_question)),
    ])


def _answer_only_follow_ups(user_question: str) -> list[str]:
    asked = _dimensions_in_question(user_question)
    lines = ["Suggested follow-ups:", "- Compare this with the prior period."]
    if "location" not in asked:
        lines.append("- Break this down by location.")
    if "product" not in asked and "category" not in asked:
        lines.append("- Break this down by product category.")
    lines.append("- Show revenue for the same period.")
    return lines


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


def _follow_up_lines(answer: dict, chart: dict, user_question: str = "") -> list[str]:
    metric = _metric_label(answer, chart).lower()
    secondary_metric = "orders" if "revenue" in metric else "revenue"
    asked = _dimensions_in_question(user_question)

    lines = [
        "Suggested follow-ups:",
        f"- Compare {metric} with the prior period.",
    ]
    if "location" not in asked:
        lines.append(f"- Break {metric} down by location.")
    elif "product" not in asked and "category" not in asked:
        lines.append(f"- Break {metric} down by product category.")
    lines.append(f"- Show {secondary_metric} for the same period.")
    return lines


def _metric_label(answer: dict, chart: dict) -> str:
    chart_metric = str(chart.get("yLabel") or chart.get("yAxis") or "").strip()
    if chart_metric:
        return _humanize_label(chart_metric)

    columns = answer.get("columns", [])
    if columns:
        return _humanize_label(str(columns[0]))

    return "this metric"



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


# ------------------------------------------------------------------
# Query state capture
# ------------------------------------------------------------------

_WHERE_PATTERN = re.compile(
    r"\bWHERE\s+(.+?)(?:\s+GROUP\b|\s+ORDER\b|\s+LIMIT\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
_ORDER_PATTERN = re.compile(
    r"\bORDER\s+BY\s+(.+?)(?:\s+LIMIT\b|$)",
    re.IGNORECASE | re.DOTALL,
)

_TIME_KEYWORDS = ("date", "day", "month", "year", "week", "period")
_METRIC_KEYWORDS = (
    "count", "sum", "avg", "total", "revenue",
    "orders", "quantity", "amount", "sales",
)


def _is_time_column(col: str, values: list) -> bool:
    if any(k in col for k in _TIME_KEYWORDS):
        return True
    return (
        bool(values)
        and isinstance(values[0], str)
        and bool(re.match(r"\d{4}-\d{2}", str(values[0])))
    )


def _is_metric_column(col: str, values: list) -> bool:
    if any(k in col for k in _METRIC_KEYWORDS):
        return True
    return (
        bool(values)
        and all(isinstance(v, (int, float)) for v in values if v is not None)
    )


def _extract_where_conditions(query: str) -> List[str]:
    m = _WHERE_PATTERN.search(query)
    if not m:
        return []
    return [c.strip() for c in re.split(r"\bAND\b", m.group(1), flags=re.IGNORECASE) if c.strip()]


def _extract_limit(query: str) -> Optional[int]:
    m = _LIMIT_PATTERN.search(query)
    return int(m.group(1)) if m else None


def _extract_order_by(query: str) -> List[str]:
    m = _ORDER_PATTERN.search(query)
    if not m:
        return []
    return [c.strip() for c in m.group(1).split(",") if c.strip()]


def _build_query_state(query: str, result: dict) -> dict:
    """Build structured query state from both the SQL query and result data."""
    columns = result.get("columns", [])
    rows = result.get("rows", [])

    metrics: List[str] = []
    dimensions: List[str] = []
    time_grain: Optional[str] = None

    for i, col in enumerate(columns):
        sample = [row[i] for row in rows[:20] if i < len(row)]
        col_lower = col.lower()

        if _is_time_column(col_lower, sample):
            if any(re.match(r"\d{4}-\d{2}$", str(v)) for v in sample if v):
                time_grain = "month"
            else:
                time_grain = "day"
            continue

        if _is_metric_column(col_lower, sample):
            metrics.append(col)
        else:
            dimensions.append(col)

    return {
        "metrics": metrics,
        "dimensions": dimensions,
        "time_grain": time_grain,
        "filters": _extract_where_conditions(query),
        "limit": _extract_limit(query),
        "sort": _extract_order_by(query),
    }


def _format_query_state(state: dict) -> str:
    """Format structured query state for injection into the user's message."""
    lines = ["Previous query state:"]
    lines.append(f"- metrics: {state.get('metrics', [])}")
    lines.append(f"- dimensions: {state.get('dimensions', [])}")
    if state.get("time_grain"):
        lines.append(f"- time_grain: {state['time_grain']}")
    if state.get("filters"):
        lines.append(f"- filters: {state['filters']}")
    if state.get("sort"):
        lines.append(f"- sort: {state['sort']}")
    if state.get("limit"):
        lines.append(f"- limit: {state['limit']}")
    lines.append("Preserve these dimensions unless the user's question implies a different grouping.")
    return "\n".join(lines)
