from __future__ import annotations

from typing import Dict, List, Optional

from app.harness.tools.base import BaseTool


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def to_openai_tools(self) -> List[dict]:
        return [tool.to_openai_schema() for tool in self._tools.values()]
