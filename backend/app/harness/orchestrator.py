from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.harness.context_manager import ContextConfig, ContextManager
from app.harness.llm.client import LLMClient
from app.harness.prompt_builder import PromptBuilder
from app.harness.sql.engine import SQLEngine
from app.harness.tool_executor import ToolExecutor
from app.harness.tool_registry import ToolRegistry
from app.harness.tools.base import ToolResult

logger = logging.getLogger(__name__)


@dataclass
class Artifact:
    type: str  # "sql" or "chart"
    query: Optional[str] = None
    result: Optional[Dict] = None
    config: Optional[Dict] = None


@dataclass
class OrchestratorResponse:
    content: str
    artifacts: List[Artifact] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        prompt_builder: PromptBuilder,
        sql_engine: SQLEngine,
        max_iterations: int = 10,
    ):
        self._llm = llm_client
        self._registry = tool_registry
        self._executor = tool_executor
        self._context_manager = context_manager
        self._prompt_builder = prompt_builder
        self._sql_engine = sql_engine
        self._max_iterations = max_iterations

    async def run_stream(
        self, messages: List[dict], context_id: str
    ) -> AsyncGenerator[str, None]:
        """Yield SSE-formatted events as the orchestrator processes."""
        context = self._context_manager.get(context_id)
        if context is None:
            yield self._sse("error", {"message": f"Unknown context: {context_id}"})
            return

        system_prompt = await self._prompt_builder.build(context, self._sql_engine)
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        tools = self._registry.to_openai_tools()
        logger.info(f"Tools registered: {[t['function']['name'] for t in tools]}")

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

            # If no tool calls, stream the final text and we're done
            if not message.tool_calls:
                content = message.content or ""
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

            # Execute each tool call and stream results
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments

                yield self._sse("status", {"message": f"Running {tool_name}..."})
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

                # Stream artifacts to client
                if result.artifact_type == "sql" and result.data:
                    args = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
                    yield self._sse("artifact", {
                        "type": "sql",
                        "query": args.get("query", ""),
                        "result": result.data,
                    })
                elif result.artifact_type == "chart" and result.data:
                    yield self._sse("artifact", {
                        "type": "chart",
                        "config": result.data,
                    })
                elif result.error:
                    yield self._sse("status", {"message": f"Error: {result.error}"})

        yield self._sse("content", {"text": "Reached maximum reasoning steps."})
        yield self._sse("done", {})

    def _sse(self, event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"
