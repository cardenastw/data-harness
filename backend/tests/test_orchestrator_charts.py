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
            visible_tables=["orders", "order_items", "products", "locations"],
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

async def _collect_events(
    orchestrator: Orchestrator,
    question: str = "How many orders did we have last month?",
    messages: list[dict] | None = None,
    turn_messages: list[dict] | None = None,
    query_state_out: list | None = None,
    prior_query_state: dict | None = None,
) -> list[tuple[str, dict]]:
    if messages is None:
        messages = [{"role": "user", "content": question}]
    events = []
    async for chunk in orchestrator.run_stream(
        messages,
        "marketing",
        turn_messages=turn_messages,
        query_state_out=query_state_out,
        prior_query_state=prior_query_state,
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

    async def test_chart_total_mismatch_still_shows_chart(self) -> None:
        """Chart data that doesn't sum to the answer is still accepted (no total gate)."""
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
            # Mismatched total (52 + 94 != 768) — should still be accepted
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 52], ["2026-03-02", 94]],
                row_count=2,
            ),
        ])

        events = await _collect_events(self._orchestrator(llm, executor, sql_engine))

        artifacts = [data for event, data in events if event == "artifact"]
        self.assertEqual([a["type"] for a in artifacts], ["sql", "chart"])
        # Only 1 chart LLM call — no retry needed
        self.assertEqual(len(llm.chart_calls), 1)

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


    async def test_text_retry_budget_exhausted_returns_error(self) -> None:
        """After MAX_TEXT_RETRIES text responses without run_sql, return a clean error (not hallucinated text)."""
        llm = FakeLLM(
            conversation_responses=[
                _final_response("Let me check that for you."),
                _final_response("Here are the results from memory."),
                _final_response("Based on previous data, there were 500 orders."),
            ],
        )
        executor = FakeExecutor([])

        events = await _collect_events(
            self._orchestrator(llm, executor, max_iterations=10)
        )

        content_events = [data for event, data in events if event == "content"]
        self.assertEqual(len(content_events), 1)
        # Should return the fixed error message, not hallucinated LLM text
        self.assertIn("try rephrasing", content_events[0]["text"])
        self.assertNotIn("500 orders", content_events[0]["text"])
        # 3 LLM calls: initial + 2 retries, then budget exhausted on 3rd text response
        self.assertEqual(len(llm.conversation_calls), 3)

    async def test_sql_errors_do_not_starve_text_retries(self) -> None:
        """SQL errors and text retries use separate budgets so the LLM gets enough SQL attempts."""
        llm = FakeLLM(
            conversation_responses=[
                # Iter 1: hallucinated text
                _final_response("Location A: 250 orders"),
                # Iter 2: forced SQL, fails
                _tool_response({"query": "SELECT bad_col FROM orders"}, "call_1"),
                # Iter 3: text explaining error
                _final_response("The column doesn't exist, let me try again."),
                # Iter 4: forced SQL, fails again
                _tool_response({"query": "SELECT bad_col2 FROM orders"}, "call_2"),
                # Iter 5: text (text_retries=3 > MAX_TEXT_RETRIES=2, but sql_errors=2 < 3 so reprompt still works)
                # Actually text_retries=3 > 2 so budget exhausted
                _final_response("I still can't get it."),
            ],
        )
        executor = FakeExecutor([
            ToolResult(error="no such column: bad_col"),
            ToolResult(error="no such column: bad_col2"),
        ])

        events = await _collect_events(
            self._orchestrator(llm, executor, max_iterations=10)
        )

        content_events = [data for event, data in events if event == "content"]
        self.assertEqual(len(content_events), 1)
        self.assertIn("try rephrasing", content_events[0]["text"])
        # LLM got 2 SQL attempts (not just 1), despite text hallucinations
        self.assertEqual(len(executor.calls), 2)

    async def test_sql_error_budget_exhausted(self) -> None:
        """After MAX_SQL_ERRORS consecutive SQL failures, stop forcing tool use."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT bad FROM orders"}, "call_1"),
                _tool_response({"query": "SELECT bad FROM orders"}, "call_2"),
                _tool_response({"query": "SELECT bad FROM orders"}, "call_3"),
                # After 3 SQL errors, tool_choice=None; LLM gives text
                _final_response("I can't query that data."),
            ],
        )
        executor = FakeExecutor([
            ToolResult(error="no such column: bad"),
            ToolResult(error="no such column: bad"),
            ToolResult(error="no such column: bad"),
        ])

        events = await _collect_events(
            self._orchestrator(llm, executor, max_iterations=10)
        )

        content_events = [data for event, data in events if event == "content"]
        self.assertEqual(len(content_events), 1)
        # Returns fixed error, not the LLM's text
        self.assertIn("try rephrasing", content_events[0]["text"])
        # 4 LLM calls: 3 failed SQL + 1 text (budget exhausted)
        self.assertEqual(len(llm.conversation_calls), 4)


    async def test_follow_ups_do_not_repeat_location_breakdown(self) -> None:
        """When user asked 'by location', suggestions should not say 'Break ... down by location'."""
        chart_sql = "SELECT l.name, COUNT(*) as orders FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY l.name"
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": chart_sql}, "call_1"),
            ],
            chart_responses=[
                _text_response(chart_sql),
            ],
        )
        executor = FakeExecutor([
            _sql_result(
                ["location", "order_count"],
                [["Downtown", 400], ["Airport", 368]],
                query=chart_sql,
            ),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(
                columns=["location", "orders"],
                rows=[["Downtown", 400], ["Airport", 368]],
                row_count=2,
            ),
        ])

        events = await _collect_events(
            self._orchestrator(llm, executor, sql_engine),
            question="How many orders did we have last month broken down by location?",
        )

        content = "\n".join(data["text"] for event, data in events if event == "content")
        self.assertNotIn("down by location", content.lower())

    async def test_follow_ups_suggest_location_for_plain_question(self) -> None:
        """When user asks a plain aggregate question, location breakdown IS suggested."""
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

        content = "\n".join(data["text"] for event, data in events if event == "content")
        self.assertIn("down by location", content.lower())

    async def test_answer_only_follow_ups_no_duplicate_location(self) -> None:
        """When chart fails and user asked 'by location', answer-only follow-ups skip location."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT l.name, COUNT(*) FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY l.name"}, "call_1"),
            ],
            chart_responses=[
                _text_response("SELECT bad FROM orders"),
                _text_response("SELECT bad FROM orders"),
                _text_response("SELECT bad FROM orders"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(
                ["location", "order_count"],
                [["Downtown", 400], ["Airport", 368]],
                query="SELECT l.name, COUNT(*) FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY l.name",
            ),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(columns=["x"], rows=[], row_count=0),
            QueryResult(columns=["x"], rows=[], row_count=0),
            QueryResult(columns=["x"], rows=[], row_count=0),
        ])

        events = await _collect_events(
            self._orchestrator(llm, executor, sql_engine),
            question="How many orders by location last month?",
        )

        content = "\n".join(data["text"] for event, data in events if event == "content")
        self.assertNotIn("down by location", content.lower())
        self.assertIn("product category", content.lower())


class SessionHistoryTests(unittest.IsolatedAsyncioTestCase):
    def _orchestrator(self, llm, executor, sql_engine=None, max_iterations=10):
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

    async def test_turn_messages_captures_tool_calls_and_results(self) -> None:
        """turn_messages should contain the assistant tool call and tool result."""
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

        turn_messages: list[dict] = []
        await _collect_events(
            self._orchestrator(llm, executor, sql_engine),
            turn_messages=turn_messages,
        )

        # Should have assistant (with tool_calls) + tool result + final assistant text
        self.assertEqual(len(turn_messages), 3)
        self.assertEqual(turn_messages[0]["role"], "assistant")
        self.assertIn("tool_calls", turn_messages[0])
        self.assertEqual(turn_messages[1]["role"], "tool")
        self.assertEqual(turn_messages[2]["role"], "assistant")
        self.assertIn("768", turn_messages[2]["content"])

    async def test_turn_messages_excludes_retry_noise(self) -> None:
        """Retry prompts and hallucinated text should NOT appear in turn_messages."""
        llm = FakeLLM(
            conversation_responses=[
                # First: hallucinated text (retry)
                _final_response("There were 500 orders."),
                # Second: forced tool call, succeeds
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

        turn_messages: list[dict] = []
        await _collect_events(
            self._orchestrator(llm, executor, sql_engine),
            turn_messages=turn_messages,
        )

        # Canonical messages: assistant (tool_calls) + tool result + final assistant text
        self.assertEqual(len(turn_messages), 3)
        # First assistant message must have tool_calls (not hallucinated text)
        self.assertIn("tool_calls", turn_messages[0])
        # No hallucinated "500 orders" in any message
        for msg in turn_messages:
            self.assertNotIn("500", msg.get("content", ""))

    async def test_follow_up_with_tool_history(self) -> None:
        """Follow-up with prior tool call history gives the LLM proper context."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response(
                    {"query": "SELECT l.name, COUNT(*) FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY l.name"},
                    "call_2",
                ),
            ],
            chart_responses=[
                _text_response("SELECT l.name, COUNT(*) as orders FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY l.name"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(
                ["location", "order_count"],
                [["Downtown", 400], ["Airport", 368]],
            ),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(
                columns=["location", "orders"],
                rows=[["Downtown", 400], ["Airport", 368]],
                row_count=2,
            ),
        ])

        # Simulate session history from a prior turn (tool calls + results, not text summaries)
        prior_history = [
            {"role": "user", "content": "How many orders last month?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "run_sql", "arguments": '{"query": "SELECT COUNT(*) FROM orders"}'}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"columns": ["count"], "rows": [[768]], "row_count": 1}'},
            {"role": "user", "content": "Break down by location"},
        ]

        turn_messages: list[dict] = []
        events = await _collect_events(
            self._orchestrator(llm, executor, sql_engine),
            messages=prior_history,
            turn_messages=turn_messages,
        )

        # LLM should have been called with the prior tool history
        llm_messages = llm.conversation_calls[0]
        tool_msgs = [m for m in llm_messages if m.get("role") == "tool"]
        # Prior tool result + new tool result (FakeLLM stores a reference to the mutated list)
        self.assertGreaterEqual(len(tool_msgs), 1)
        # The prior turn's result (768) must be present in the history
        prior_tool = [m for m in tool_msgs if "768" in m.get("content", "")]
        self.assertEqual(len(prior_tool), 1)

        # Should succeed (artifacts emitted)
        artifacts = [data for event, data in events if event == "artifact"]
        self.assertTrue(len(artifacts) >= 1)

        # New turn messages captured: tool call + tool result + final text
        self.assertEqual(len(turn_messages), 3)


    async def test_query_state_captures_dimensions_from_result(self) -> None:
        """Query state should detect dimensions and metrics from result column data."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response(
                    {"query": "SELECT l.name AS location, COUNT(*) AS order_count FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY l.name ORDER BY order_count DESC"},
                    "call_1",
                ),
            ],
            chart_responses=[
                _text_response("SELECT l.name AS location, COUNT(*) AS orders FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY l.name"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(
                ["location", "order_count"],
                [["Downtown", 400], ["Airport", 368]],
                query="SELECT l.name AS location, COUNT(*) AS order_count FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY l.name ORDER BY order_count DESC",
            ),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(
                columns=["location", "orders"],
                rows=[["Downtown", 400], ["Airport", 368]],
                row_count=2,
            ),
        ])

        query_state_out: list = []
        await _collect_events(
            self._orchestrator(llm, executor, sql_engine),
            query_state_out=query_state_out,
        )

        self.assertEqual(len(query_state_out), 1)
        state = query_state_out[0]
        self.assertIn("location", state["dimensions"])
        self.assertIn("order_count", state["metrics"])
        self.assertIsNone(state["time_grain"])
        self.assertIsInstance(state["sort"], list)
        self.assertTrue(len(state["sort"]) > 0)

    async def test_query_state_injects_on_follow_up(self) -> None:
        """Prior query state should be injected into the user's message on follow-up."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT strftime('%Y-%m', order_date) AS month, l.name AS location, COUNT(*) AS order_count FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY month, l.name"}, "call_1"),
            ],
            chart_responses=[
                _text_response("SELECT strftime('%Y-%m', order_date) AS month, l.name AS location, COUNT(*) AS orders FROM orders o JOIN locations l ON o.location_id = l.id GROUP BY month, l.name"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(
                ["month", "location", "order_count"],
                [["2026-01", "Downtown", 200], ["2026-02", "Downtown", 250]],
            ),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(
                columns=["month", "location", "orders"],
                rows=[["2026-01", "Downtown", 200], ["2026-02", "Downtown", 250]],
                row_count=2,
            ),
        ])

        prior_state = {
            "metrics": ["order_count"],
            "dimensions": ["location"],
            "time_grain": None,
            "filters": [],
            "limit": None,
            "sort": [],
        }

        await _collect_events(
            self._orchestrator(llm, executor, sql_engine),
            question="now show me this month over month",
            prior_query_state=prior_state,
        )

        # The LLM should have received the user's message with injected state
        llm_messages = llm.conversation_calls[0]
        last_user = [m for m in llm_messages if m.get("role") == "user"][-1]
        self.assertIn("dimensions", last_user["content"])
        self.assertIn("location", last_user["content"])


    async def test_duplicate_sql_short_circuits(self) -> None:
        """When the LLM sends the same run_sql query twice, skip execution the second time."""
        llm = FakeLLM(
            conversation_responses=[
                # First: run_sql with bad query
                _tool_response({"query": "SELECT bad_col FROM orders"}, "call_1"),
                # Second: same query again
                _tool_response({"query": "SELECT bad_col FROM orders"}, "call_2"),
                # Third: different query, succeeds
                _tool_response({"query": "SELECT COUNT(*) AS order_count FROM orders"}, "call_3"),
            ],
            chart_responses=[
                _text_response("SELECT date(order_date) as day, COUNT(*) as orders FROM orders GROUP BY day"),
            ],
        )
        executor = FakeExecutor([
            ToolResult(error="no such column: bad_col"),
            # Second call should NOT reach executor (dedup)
            _sql_result(["order_count"], [[768]]),
        ])
        sql_engine = FakeSQLEngine([
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 400], ["2026-03-02", 368]],
                row_count=2,
            ),
        ])

        events = await _collect_events(
            self._orchestrator(llm, executor, sql_engine),
        )

        # Executor should only be called twice (1st bad + 3rd good), not three times
        self.assertEqual(len(executor.calls), 2)
        # Should still succeed in the end
        artifacts = [data for event, data in events if event == "artifact"]
        self.assertTrue(len(artifacts) >= 1)

    async def test_chart_duplicate_sql_skips_execution(self) -> None:
        """When chart loop generates the same SQL twice, skip execution the second time."""
        llm = FakeLLM(
            conversation_responses=[
                _tool_response({"query": "SELECT COUNT(*) FROM orders"}, "call_1"),
            ],
            chart_responses=[
                # Same chart query twice, then a different one
                _text_response("SELECT month, COUNT(*) FROM orders GROUP BY month"),
                _text_response("SELECT month, COUNT(*) FROM orders GROUP BY month"),
                _text_response("SELECT date(order_date) as day, COUNT(*) as orders FROM orders GROUP BY day"),
            ],
        )
        executor = FakeExecutor([
            _sql_result(["order_count"], [[768]]),
        ])
        sql_engine = FakeSQLEngine([
            # First chart attempt: fails (1 row)
            QueryResult(columns=["month", "orders"], rows=[["2026-03", 768]], row_count=1),
            # Second attempt is duplicate — should NOT execute
            # Third attempt: succeeds
            QueryResult(
                columns=["day", "orders"],
                rows=[["2026-03-01", 400], ["2026-03-02", 368]],
                row_count=2,
            ),
        ])

        events = await _collect_events(self._orchestrator(llm, executor, sql_engine))

        # SQL engine should have executed only 2 chart queries (not 3)
        self.assertEqual(len(sql_engine.executed), 2)


class DimensionDetectionTests(unittest.TestCase):
    def test_by_location(self) -> None:
        from app.harness.orchestrator import _dimensions_in_question
        self.assertIn("location", _dimensions_in_question("orders by location"))

    def test_broken_down_by(self) -> None:
        from app.harness.orchestrator import _dimensions_in_question
        self.assertIn("location", _dimensions_in_question("broken down by location"))

    def test_per_product(self) -> None:
        from app.harness.orchestrator import _dimensions_in_question
        self.assertIn("product", _dimensions_in_question("revenue per product"))

    def test_no_dimension(self) -> None:
        from app.harness.orchestrator import _dimensions_in_question
        self.assertEqual(set(), _dimensions_in_question("How many orders last month?"))

    def test_for_each(self) -> None:
        from app.harness.orchestrator import _dimensions_in_question
        self.assertIn("store", _dimensions_in_question("sales for each store"))


if __name__ == "__main__":
    unittest.main()
