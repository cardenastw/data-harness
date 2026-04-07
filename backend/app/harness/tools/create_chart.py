from __future__ import annotations

from typing import Any

from .base import BaseTool, ToolResult

VALID_CHART_TYPES = {"bar", "line", "pie", "area", "scatter"}


class CreateChartTool(BaseTool):
    name = "create_chart"
    description = (
        "Create a chart visualization. Call this after running a SQL query to "
        "visualize the results. Provide the chart type, data, and axis configuration."
    )
    parameters = {
        "type": "object",
        "properties": {
            "chart_type": {
                "type": "string",
                "enum": list(VALID_CHART_TYPES),
                "description": "The type of chart to create",
            },
            "title": {
                "type": "string",
                "description": "Chart title",
            },
            "data": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Array of data objects. Each object is one data point with keys matching axis fields.",
            },
            "x_axis": {
                "type": "string",
                "description": "Key in data objects for x-axis (or category for pie charts)",
            },
            "y_axis": {
                "type": "string",
                "description": "Key in data objects for y-axis (or value for pie charts)",
            },
            "x_label": {
                "type": "string",
                "description": "Custom label for x-axis",
            },
            "y_label": {
                "type": "string",
                "description": "Custom label for y-axis",
            },
        },
        "required": ["chart_type", "title", "data", "x_axis", "y_axis"],
    }

    async def execute(self, arguments: dict, context: Any) -> ToolResult:
        chart_type = arguments.get("chart_type", "bar")
        if chart_type not in VALID_CHART_TYPES:
            return ToolResult(error=f"Invalid chart type: {chart_type}. Must be one of {VALID_CHART_TYPES}")

        colors = getattr(context, "chart_preferences", {})
        if isinstance(colors, dict):
            palette = colors.get("color_palette", ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#65a30d"])
        else:
            palette = getattr(colors, "color_palette", ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#65a30d"])

        chart_config = {
            "chartType": chart_type,
            "title": arguments["title"],
            "data": arguments["data"],
            "xAxis": arguments["x_axis"],
            "yAxis": arguments["y_axis"],
            "xLabel": arguments.get("x_label", arguments["x_axis"]),
            "yLabel": arguments.get("y_label", arguments["y_axis"]),
            "colors": palette,
        }
        return ToolResult(data=chart_config, artifact_type="chart")
