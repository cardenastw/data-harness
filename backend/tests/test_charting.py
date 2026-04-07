from __future__ import annotations

import unittest

from app.harness.charting import build_auto_chart, validate_chart_data


class ChartingTests(unittest.TestCase):
    def test_date_and_numeric_rows_produce_line_chart(self) -> None:
        result = build_auto_chart(
            ["day", "orders"],
            [["2026-03-01", 25], ["2026-03-02", 23]],
            {"color_palette": ["#111111"]},
        )

        self.assertIsNone(result.error)
        self.assertIsNotNone(result.chart)
        assert result.chart is not None
        self.assertEqual(result.chart["chartType"], "line")
        self.assertEqual(result.chart["xAxis"], "day")
        self.assertEqual(result.chart["yAxis"], "orders")
        self.assertEqual(result.chart["colors"], ["#111111"])

    def test_category_and_numeric_rows_produce_bar_chart(self) -> None:
        result = build_auto_chart(
            ["tier", "members"],
            [["bronze", 12], ["silver", 8], ["gold", 4]],
        )

        self.assertIsNone(result.error)
        self.assertIsNotNone(result.chart)
        assert result.chart is not None
        self.assertEqual(result.chart["chartType"], "bar")

    def test_single_aggregate_row_is_rejected(self) -> None:
        result = validate_chart_data(["month", "orders"], [["2026-03", 768]])

        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("at least 2 rows", result.error)

    def test_numeric_strings_are_accepted_and_converted(self) -> None:
        result = build_auto_chart(
            ["tier", "members"],
            [["bronze", "12"], ["silver", "8"]],
        )

        self.assertIsNone(result.error)
        self.assertIsNotNone(result.chart)
        assert result.chart is not None
        self.assertEqual(result.chart["data"][0]["members"], 12)
        self.assertEqual(result.chart["data"][1]["members"], 8)


if __name__ == "__main__":
    unittest.main()
