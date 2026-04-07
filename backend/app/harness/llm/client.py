from __future__ import annotations

from typing import List, Optional, Union

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion


class LLMClient:
    def __init__(self, base_url: str, model: str):
        self._client = AsyncOpenAI(base_url=base_url, api_key="ollama")
        self._model = model

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
