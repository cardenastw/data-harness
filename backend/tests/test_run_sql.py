from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.harness.sql.engine import QueryResult
from app.harness.sql.safety import SQLSafetyValidator
from app.harness.tools.run_sql import RunSQLTool


class FakeEngine:
    async def execute_query(
        self,
        sql: str,
        timeout_seconds: float = 30.0,
        max_rows: int = 500,
    ) -> QueryResult:
        if "fail" in sql:
            raise RuntimeError("query exploded")
        return QueryResult(
            columns=["total_orders"],
            rows=[[768]],
            row_count=1,
        )


class RunSQLToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tool = RunSQLTool(
            FakeEngine(),
            SQLSafetyValidator(),
            SimpleNamespace(sql_query_timeout=30.0, sql_max_rows=500),
        )
        self.context = SimpleNamespace(visible_tables=[])

    async def test_successful_query(self) -> None:
        result = await self.tool.execute({"query": "SELECT 768"}, self.context)

        self.assertIsNone(result.error)
        self.assertEqual(result.artifact_type, "sql")
        self.assertEqual(result.data["columns"], ["total_orders"])
        self.assertEqual(result.data["rows"], [[768]])
        self.assertEqual(result.data["query"], "SELECT 768")

    async def test_empty_query_returns_error(self) -> None:
        result = await self.tool.execute({"query": ""}, self.context)

        self.assertIsNotNone(result.error)
        self.assertIn("No query", result.error)

    async def test_missing_query_returns_error(self) -> None:
        result = await self.tool.execute({}, self.context)

        self.assertIsNotNone(result.error)
        self.assertIn("No query", result.error)

    async def test_query_execution_error(self) -> None:
        result = await self.tool.execute(
            {"query": "SELECT fail"}, self.context
        )

        self.assertIsNotNone(result.error)
        self.assertIn("query exploded", result.error)

    async def test_unsafe_query_rejected(self) -> None:
        result = await self.tool.execute(
            {"query": "DROP TABLE orders"}, self.context
        )

        self.assertIsNotNone(result.error)
        self.assertIn("rejected", result.error)

    async def test_unauthorized_table_rejected(self) -> None:
        ctx = SimpleNamespace(visible_tables=["products"])
        result = await self.tool.execute(
            {"query": "SELECT * FROM orders"}, ctx
        )

        self.assertIsNotNone(result.error)
        self.assertIn("Access denied", result.error)


if __name__ == "__main__":
    unittest.main()
