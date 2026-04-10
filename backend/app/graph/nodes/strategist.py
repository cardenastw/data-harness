from __future__ import annotations

import logging
import re
from typing import Any

from app.graph.state import GraphState

logger = logging.getLogger(__name__)

def _extract_usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


STRATEGIST_SYSTEM_PROMPT = """\
You are a data analyst assistant. Given the user's question, the SQL query that answered it, \
and a summary of the results, suggest 2-3 natural follow-up questions the user might want to ask.

Return ONLY the follow-up questions, one per line, prefixed with "- ". No other text.

Example:
- How does this compare to the previous month?
- Which location contributed the most?
- What's the trend over the last 3 months?
"""


def strategist_node(llm_client: Any):
    async def _run(state: GraphState) -> dict:
        raw_data = state.get("raw_data")
        if not raw_data:
            return {"suggestions": [], "token_usage": []}

        user_question = state["user_question"]
        sql = state.get("generated_sql", "")

        row_count = raw_data.get("row_count", 0)
        columns = raw_data.get("columns", [])
        preview = raw_data.get("rows", [])[:5]
        result_summary = (
            f"Query returned {row_count} rows with columns: {', '.join(columns)}. "
            f"First rows: {preview}"
        )

        try:
            response = await llm_client.chat_completion([
                {"role": "system", "content": STRATEGIST_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"User question: {user_question}\n"
                    f"SQL: {sql}\n"
                    f"Result summary: {result_summary}"
                )},
            ])
            usage_entry = _extract_usage(response)
            raw = response.choices[0].message.content or ""

            # Parse bullet-point suggestions
            suggestions = [
                line.lstrip("- ").strip()
                for line in raw.strip().splitlines()
                if line.strip().startswith("-")
            ]
            if not suggestions:
                # Fallback: just split on newlines
                suggestions = [s.strip() for s in raw.strip().splitlines() if s.strip()]

            suggestions = suggestions[:3]
            logger.info("Strategist generated %d suggestions", len(suggestions))
            return {"suggestions": suggestions, "token_usage": [usage_entry]}

        except Exception:
            logger.exception("Strategist LLM call failed")
            return {"suggestions": [], "token_usage": []}

    return _run
