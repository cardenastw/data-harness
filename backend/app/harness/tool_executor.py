from __future__ import annotations

import json
from typing import Any

from app.harness.tool_registry import ToolRegistry
from app.harness.tools.base import ToolResult


class ToolExecutor:
    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    async def execute(self, tool_name: str, arguments_str: str, context: Any) -> ToolResult:
        tool = self._registry.get(tool_name)
        if tool is None:
            return ToolResult(error=f"Unknown tool: {tool_name}")

        try:
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError as e:
            return ToolResult(error=f"Invalid tool arguments: {e}")

        try:
            return await tool.execute(arguments, context)
        except Exception as e:
            return ToolResult(error=f"Tool execution failed: {e}")
