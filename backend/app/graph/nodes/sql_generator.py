from __future__ import annotations

import logging
import re
from typing import Any

from app.graph.state import GraphState

logger = logging.getLogger(__name__)


def _extract_sql(text: str) -> str:
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _extract_usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def sql_generator_node(llm_client: Any):
    async def _run(state: GraphState) -> dict:
        system_prompt = state["system_prompt"]
        user_question = state["user_question"]
        session_messages = state.get("session_messages", [])

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(session_messages)

        # Build user message with optional error feedback (include the failed SQL)
        user_content = user_question
        failed_sql = state.get("generated_sql", "")
        if state.get("validation_error"):
            user_content += (
                f"\n\nYour previous SQL was rejected."
                f"\n  SQL: {failed_sql}"
                f"\n  Error: {state['validation_error']}"
                f"\nWrite a corrected query that fixes this error."
            )
        elif state.get("execution_error"):
            user_content += (
                f"\n\nYour previous SQL failed at execution."
                f"\n  SQL: {failed_sql}"
                f"\n  Error: {state['execution_error']}"
                f"\nWrite a corrected query that fixes this error."
            )

        messages.append({"role": "user", "content": user_content})

        attempts = state.get("sql_attempts", 0)
        logger.info("SQL generation attempt %d", attempts + 1)

        response = await llm_client.chat_completion(messages)
        raw = response.choices[0].message.content or ""
        sql = _extract_sql(raw)

        # Strip trailing semicolons for safety
        sql = sql.rstrip(";")

        logger.info("Generated SQL: %s", sql[:200])

        return {
            "generated_sql": sql,
            "validation_error": None,
            "execution_error": None,
            "sql_attempts": attempts + 1,
            "token_usage": [_extract_usage(response)],
        }

    return _run
