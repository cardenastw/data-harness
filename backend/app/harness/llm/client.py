from __future__ import annotations

from typing import List, Optional, Union

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion


class LLMClient:
    def __init__(self, base_url: str, model: str):
        self._client = AsyncOpenAI(base_url=base_url, api_key="ollama")
        self._model = model

    @staticmethod
    def extract_usage(response: ChatCompletion) -> tuple[int, int]:
        """Return (prompt_tokens, completion_tokens), defaulting to 0 if absent."""
        usage = getattr(response, "usage", None)
        if usage:
            return (getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0)
        return (0, 0)

    async def chat_completion(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        tool_choice: Optional[Union[dict, str]] = None,
    ) -> ChatCompletion:
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        return await self._client.chat.completions.create(**kwargs)
