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
        session_messages = state.get("session_messages", [])
        current = state.get("_current_subtask") or {}
        subtask_id = current.get("subtask_id", "?")
        question = current.get("question") or state.get("user_question", "")

        # _current_subtask is the snapshot from Send time. For retry feedback we
        # need the freshest validator/executor errors, which live in the merged
        # subtasks list.
        latest = current
        for st in state.get("subtasks", []) or []:
            if st.get("subtask_id") == subtask_id:
                latest = {**current, **st}
                break

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(session_messages)

        user_content = question
        failed_sql = latest.get("generated_sql", "")
        if latest.get("validation_error"):
            user_content += (
                f"\n\nYour previous SQL was rejected."
                f"\n  SQL: {failed_sql}"
                f"\n  Error: {latest['validation_error']}"
                f"\nWrite a corrected query that fixes this error."
            )
        elif latest.get("execution_error"):
            exec_err = latest["execution_error"]
            user_content += (
                f"\n\nYour previous SQL failed at execution."
                f"\n  SQL: {failed_sql}"
                f"\n  Error: {exec_err}"
                f"\nWrite a corrected query that fixes this error."
            )
            # SQLite-specific hint when the error looks like Postgres-flavored SQL
            # leaked through. The base prompt already says avoid these, but small
            # models often need the reminder right next to the failure.
            err_lower = exec_err.lower()
            if (
                "unrecognized token" in err_lower
                or "no such function" in err_lower
                or "syntax error" in err_lower
            ):
                user_content += (
                    "\nReminder: this is SQLite, NOT PostgreSQL. Common offenders:"
                    "\n  - `::type` casts (PostgreSQL) — use CAST(value AS TYPE) or remove."
                    "\n  - DATE_TRUNC, EXTRACT, NOW(), INTERVAL — replace with date(), strftime(),"
                    "    date('now'), and date('now','-N days'/'-N months')."
                    "\n  - Bare `:name` parameter placeholders — write the literal value inline."
                    "\n  - Fancy quoting like E'...' or $$...$$ — use plain single quotes."
                    "\n  - `= ANY (subquery)` or `= ALL (subquery)` (PostgreSQL) — use `IN (subquery)`"
                    "    or rewrite as a JOIN."
                    "\n  - Window function syntax differs slightly — stick to plain aggregates"
                    "    (SUM/COUNT/AVG with GROUP BY) unless you really need a window."
                    "\n  - String concat `||` works; `CONCAT(...)` does not exist in SQLite."
                    "\n  - Boolean true/false literals — use 1 and 0."
                )
            if "same number of result columns" in err_lower or "union" in err_lower:
                user_content += (
                    "\nReminder: each branch of UNION/UNION ALL must SELECT the SAME number of"
                    " columns in the SAME order. Do NOT use `SELECT *` when UNIONing two"
                    " tables with different schemas (e.g. orders and cart_orders) — instead,"
                    " pick the specific columns you need and project NULLs (or constants)"
                    " for any column missing from one side. Example pattern:"
                    "\n    SELECT order_date, total FROM orders WHERE ..."
                    "\n    UNION ALL"
                    "\n    SELECT order_date, total FROM cart_orders WHERE ..."
                )
            if "no such column" in err_lower:
                user_content += (
                    "\nReminder: only reference columns that exist on the table you SELECT"
                    " FROM in that scope. CTE columns must be projected by an inner SELECT"
                    " before the outer query can reference them."
                )

        messages.append({"role": "user", "content": user_content})

        attempts = latest.get("sql_attempts", 0)
        logger.info("SQL generation attempt %d for subtask %s", attempts + 1, subtask_id)

        response = await llm_client.chat_completion(messages)
        raw = response.choices[0].message.content or ""
        sql = _extract_sql(raw)

        # Strip trailing semicolons for safety
        sql = sql.rstrip(";")

        logger.info("Generated SQL [%s]: %s", subtask_id, sql[:200])

        return {
            "subtasks": [
                {
                    "subtask_id": subtask_id,
                    "generated_sql": sql,
                    "validation_error": None,
                    "execution_error": None,
                    "sql_attempts": attempts + 1,
                }
            ],
            "token_usage": [_extract_usage(response)],
        }

    return _run
