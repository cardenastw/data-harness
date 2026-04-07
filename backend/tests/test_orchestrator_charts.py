from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

try:
    from app.harness.orchestrator import Orchestrator
except ModuleNotFoundError as exc:
    if exc.name in {"openai", "yaml"}:
        raise unittest.SkipTest(f"Missing optional backend dependency: {exc.name}")
    raise

from app.harness.sql.engine import ColumnInfo, QueryResult
from app.harness.sql.safety import SQLSafetyValidator
from app.harness.tools.base import ToolResult

INTERNAL_TERMS = ("chart_query", "run_sql", "tool", "retry", "SQL")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _tool_response(arguments: dict, call_id: str) -> SimpleNamespace:
    tool_call = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="run_sql",
            arguments=json.dumps(arguments),
        ),
    )
    message = SimpleNamespace(content="", tool_calls=[tool_call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")]
    )


def _final_response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")]
    )


def _text_response(content: str) -> SimpleNamespace:
    """LLM response with just text (no tool calls) — used for chart query generation."""
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")]
    )


def _sql_result(columns: list[str], rows: list[list], query: str = "SELECT COUNT(*) AS total_orders FROM orders") -> ToolResult:
    return ToolResult(
        artifact_type="sql",
        data={
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
            "execution_time_ms": 1.0,
            "query": query,
        },
    )


class FakeLLM:
    """Fake that returns canned responses. Distinguishes between main conversation
    calls (with tools) and chart generation calls (no tools)."""

    def __init__(
        self,
        conversation_responses: list[SimpleNamespace],
        chart_responses: list[SimpleNamespace] | None = None,
    ):
        self.conversation_responses = list(conversation_responses)
        self.chart_responses = list(chart_responses or [])
        self.conversation_calls: list[list[dict]] = []
        self.chart_calls: list[list[dict]] = []

    async def chat_completion(self, messages, tools=None, tool_choice=None):
        if tools:
            self.conversation_calls.append(messages)
            return self.conversation_responses.pop(0)
        else:
            self.chart_calls.append(messages)
            return self.chart_responses.pop(0)


class FakeExecutor:
    def __init__(self, results: list[ToolResult]):
        self.results = list(results)
        self.calls: list[dict] = []

    async def execute(self, tool_name, arguments_str, context):
        self.calls.append(json.loads(arguments_str))
        return self.results.pop(0)


class FakeRegistry:
    def to_openai_tools(self):
        return [{"type": "function", "function": {"name": "run_sql"}}]


class FakePromptBuilder:
    async def build(self, context, sql_engine):
        return "system prompt"


class FakeContextManager:
    def __init__(self):
        self.context = SimpleNamespace(
            chart_preferences=SimpleNamespace(color_palette=["#123456"]),
            visible_tables=["orders", "order_items", "products"],
        )

    def get(self, context_id):
        return self.context


class FakeSQLEngine:
    """Returns canned chart query results."""

    def __init__(self, results: list[QueryResult | Exception] | None = None):
        self._results = list(results or [])
        self.executed: list[str] = []

    async def execute_query(self, sql, timeout_seconds=30.0, max_rows=500):
        self.executed.append(sql)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def get_columns(self, table_name: str) -> list[ColumnInfo]:
        schemas = {
            "orders": [
                ColumnInfo(name="id", data_type="INTEGER", is_primary_key=True),
                ColumnInfo(name="order_date", data_type="DATETIME"),
                ColumnInfo(name="total", data_type="REAL"),
                ColumnInfo(name="status", data_type="TEXT"),
                ColumnInfo(name="location_id", data_type="INTEGER"),
            ],
            "order_items": [
                ColumnInfo(name="id", data_type="INTEGER", is_primary_key=True),
                ColumnInfo(name="order_id", data_type="INTEGER"),
                ColumnInfo(name="product_id", data_type="INTEGER"),
                ColumnInfo(name="quantity", data_type="INTEGER"),
            ],
            "products": [
                ColumnInfo(name="id", data_type="INTEGER", is_primary_key=True),
                ColumnInfo(name="name", data_type="TEXT"),
                ColumnInfo(name="category", data_type="TEXT"),
            ],
        }
        return schemas.get(table_name, [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect_events(orchestrator: Orchestrator) -> list[tuple[str, dict]]:
    events = []
    async for chunk in orchestrator.run_stream(
        [{"role": "user", "content": "How many orders did we have last month?"}],
        "marketing",
    ):
        lines = chunk.strip().splitlines()
        event_type = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((event_type, data))
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class OrchestratorChartTests(unittest.IsolatedAsyncioTestCase):
    def _orchestrator(
        self,
        llm: FakeLLM,
        executor: FakeExecutor,
        sql_engine: FakeSQLEngine | None = None,
        max_iterations: int = 3,
    ) -> Orchestrator:
        return Orchestrator(
            llm_client=llm,
            tool_registry=FakeRegistry(),
            tool_executor=executor,
            context_manager=FakeContextManager(),
            prompt_builder=FakePromptBuilder(),
            sql_engine=sql_engine or FakeSQLEngine(),
            sql_safety_validator=SQLSafetyValidator(),
            settings=SimpleNamespace(sql_query_timeout=30.0, sql_max_rows=500),
            max_iterations=max_iterations,
        )

    def assert_no_internal_terms(self, text: str) -> None:
        for term in INTERNAL_TERMS:
            self.assertNotIn(term, text)

    async def test_successful_chart_streams_sql_and_chart_artifacts(self) -> None:
        """Answer query succeeds, chart LLM generates good SQL, chart is built."""
        chart_sql = "SELECT date(order_date) as day, COUNT(*) as orders FROM orders GROUP BY day"
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT COUNT(*) FROM orders"}, "call_1"),
            ],
            chart_responses=[
                _text_response(chart_sql),
            ],
        )
        executor = FakeExecutor([
            _sql_result(["total_orders"], [[768]]),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 400], ["2026-03-02", 368]],
                row_count=2,
            ),
        ])

        events = await _collect_events(self._orchestrator(llm, executor, sql_engine))

        artifacts = [data for event, data in events if event == "artifact"]
        content = "\n".join(data["text"] for event, data in events if event == "content")
        self.assertEqual([a["type"] for a in artifacts], ["sql", "chart"])
        self.assertEqual(artifacts[0]["result"]["rows"], [[768]])
        self.assertEqual(artifacts[1]["config"]["chartType"], "line")
        self.assertEqual(artifacts[1]["config"]["colors"], ["#123456"])
        self.assertIn("Total Orders: 768.", content)
        self.assert_no_internal_terms(content)

    async def test_chart_generation_retry_on_single_row(self) -> None:
        """First chart attempt returns 1 row (fails validation), second attempt succeeds."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT COUNT(*) FROM orders"}, "call_1"),
            ],
            chart_responses=[
                _text_response("SELECT strftime('%Y-%m', order_date) as month, COUNT(*) FROM orders GROUP BY month"),
                _text_response("SELECT date(order_date) as day, COUNT(*) as orders FROM orders GROUP BY day"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(["total_orders"], [[768]]),
        ])
        sql_engine = FakeSQLEngine([
            # First chart attempt: 1 row (fails)
            QueryResult(columns=["month", "orders"], rows=[["2026-03", 768]], row_count=1),
            # Second chart attempt: multiple rows (succeeds)
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 400], ["2026-03-02", 368]],
                row_count=2,
            ),
        ])

        events = await _collect_events(self._orchestrator(llm, executor, sql_engine))

        artifacts = [data for event, data in events if event == "artifact"]
        self.assertEqual([a["type"] for a in artifacts], ["sql", "chart"])
        self.assertEqual(artifacts[1]["config"]["data"][0]["day"], "2026-03-01")
        # Two chart LLM calls were made
        self.assertEqual(len(llm.chart_calls), 2)

    async def test_chart_failure_falls_back_to_answer_only(self) -> None:
        """When all chart attempts fail, answer is still streamed without chart."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT COUNT(*) FROM orders"}, "call_1"),
            ],
            chart_responses=[
                _text_response("SELECT month, COUNT(*) FROM orders GROUP BY month"),
                _text_response("SELECT month, COUNT(*) FROM orders GROUP BY month"),
                _text_response("SELECT month, COUNT(*) FROM orders GROUP BY month"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(["total_orders"], [[768]]),
        ])
        sql_engine = FakeSQLEngine([
            # All 3 chart attempts return 1 row
            QueryResult(columns=["month", "orders"], rows=[["2026-03", 768]], row_count=1),
            QueryResult(columns=["month", "orders"], rows=[["2026-03", 768]], row_count=1),
            QueryResult(columns=["month", "orders"], rows=[["2026-03", 768]], row_count=1),
        ])

        events = await _collect_events(self._orchestrator(llm, executor, sql_engine))

        artifacts = [data for event, data in events if event == "artifact"]
        content = "\n".join(data["text"] for event, data in events if event == "content")
        self.assertEqual([a["type"] for a in artifacts], ["sql"])
        self.assertIn("Total Orders: 768.", content)
        self.assert_no_internal_terms(content)

    async def test_chart_total_mismatch_triggers_retry(self) -> None:
        """Chart data that doesn't sum to the answer value is retried."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT COUNT(*) FROM orders"}, "call_1"),
            ],
            chart_responses=[
                _text_response("SELECT day, COUNT(*) as orders FROM orders GROUP BY day"),
                _text_response("SELECT day, COUNT(*) as orders FROM orders GROUP BY day"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(["total_orders"], [[768]]),
        ])
        sql_engine = FakeSQLEngine([
            # First: mismatched total (52 + 94 != 768)
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 52], ["2026-03-02", 94]],
                row_count=2,
            ),
            # Second: correct total (400 + 368 = 768)
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 400], ["2026-03-02", 368]],
                row_count=2,
            ),
        ])

        events = await _collect_events(self._orchestrator(llm, executor, sql_engine))

        artifacts = [data for event, data in events if event == "artifact"]
        self.assertEqual([a["type"] for a in artifacts], ["sql", "chart"])
        self.assertEqual(len(llm.chart_calls), 2)

    async def test_no_internal_terms_in_status_messages(self) -> None:
        """Status messages should not leak internal terminology."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT COUNT(*) FROM orders"}, "call_1"),
            ],
            chart_responses=[
                _text_response("SELECT day, COUNT(*) as orders FROM orders GROUP BY day"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(["total_orders"], [[768]]),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 400], ["2026-03-02", 368]],
                row_count=2,
            ),
        ])

        events = await _collect_events(self._orchestrator(llm, executor, sql_engine))

        statuses = [data["message"] for event, data in events if event == "status"]
        self.assert_no_internal_terms("\n".join(statuses))

    async def test_chart_llm_is_separate_from_conversation(self) -> None:
        """Chart query is generated by a separate LLM call without tools."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT COUNT(*) FROM orders"}, "call_1"),
            ],
            chart_responses=[
                _text_response("SELECT day, COUNT(*) as orders FROM orders GROUP BY day"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(["total_orders"], [[768]]),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 400], ["2026-03-02", 368]],
                row_count=2,
            ),
        ])

        await _collect_events(self._orchestrator(llm, executor, sql_engine))

        # Conversation LLM was called once (with tools)
        self.assertEqual(len(llm.conversation_calls), 1)
        # Chart LLM was called once (without tools)
        self.assertEqual(len(llm.chart_calls), 1)
        # Chart call should NOT have tools in its messages
        chart_messages = llm.chart_calls[0]
        self.assertEqual(chart_messages[0]["role"], "system")
        self.assertIn("chart", chart_messages[0]["content"].lower())


if __name__ == "__main__":
    unittest.main()
