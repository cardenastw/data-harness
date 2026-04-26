from __future__ import annotations

import logging
from typing import Any

from app.graph.state import GraphState, SubtaskResult

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
You are a data analyst assistant. Given the user's original question and the SQL queries that were run \
to answer it (with result summaries), suggest 2-3 natural follow-up questions the user might want to ask.

Cover the breadth of what was looked up — if multiple queries ran, the suggestions should explore the \
full picture, not just one query's output.

Return ONLY the follow-up questions, one per line, prefixed with "- ". No other text.

Example:
- How does this compare to the previous month?
- Which location contributed the most?
- What's the trend over the last 3 months?
"""


def _summarize_sql_subtask(st: SubtaskResult) -> str:
    sid = st.get("subtask_id", "?")
    q = st.get("question", "")
    sql = st.get("generated_sql", "")
    raw = st.get("raw_data") or {}
    rc = raw.get("row_count", 0) if raw else 0
    cols = raw.get("columns", []) if raw else []
    preview = raw.get("rows", [])[:3] if raw else []
    err = st.get("error") or st.get("execution_error")
    if err:
        return f"[{sid}] {q!r} → SQL: {sql} → ERROR: {err}"
    return (
        f"[{sid}] {q!r}\n  SQL: {sql}\n  result: {rc} rows; "
        f"columns={cols}; first={preview}"
    )


def strategist_node(llm_client: Any):
    async def _run(state: GraphState) -> dict:
        subtasks: list[SubtaskResult] = list(state.get("subtasks", []) or [])
        sql_subtasks = [
            st for st in subtasks if st.get("type") == "sql" and st.get("raw_data")
        ]

        if not sql_subtasks:
            return {"suggestions": [], "token_usage": []}

        user_question = state["user_question"]
        bundle = "\n\n".join(_summarize_sql_subtask(st) for st in sql_subtasks)

        try:
            response = await llm_client.chat_completion([
                {"role": "system", "content": STRATEGIST_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"User question: {user_question}\n\n"
                    f"Subtasks that ran:\n{bundle}"
                )},
            ])
            usage_entry = _extract_usage(response)
            raw = response.choices[0].message.content or ""

            suggestions = [
                line.lstrip("- ").strip()
                for line in raw.strip().splitlines()
                if line.strip().startswith("-")
            ]
            if not suggestions:
                suggestions = [s.strip() for s in raw.strip().splitlines() if s.strip()]

            suggestions = suggestions[:3]
            logger.info("Strategist generated %d suggestions", len(suggestions))
            return {"suggestions": suggestions, "token_usage": [usage_entry]}

        except Exception:
            logger.exception("Strategist LLM call failed")
            return {"suggestions": [], "token_usage": []}

    return _run
