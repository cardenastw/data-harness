from __future__ import annotations

import logging
import re

from app.graph.state import GraphState
from app.sql.safety import SQLSafetyValidator

logger = logging.getLogger(__name__)

_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+[\"'`]?(\w+)[\"'`]?", re.IGNORECASE
)

# CTE declarations: `WITH foo AS (...)` or `, bar AS (...)`. Matches the name
# preceding `AS (`. Greedy on the keyword boundary; tolerant of whitespace.
_CTE_NAME_PATTERN = re.compile(
    r"\b(\w+)\s+AS\s*\(\s*(?:SELECT|WITH|VALUES)\b",
    re.IGNORECASE,
)


def validator_node(safety: SQLSafetyValidator):
    async def _run(state: GraphState) -> dict:
        current = state.get("_current_subtask") or {}
        subtask_id = current.get("subtask_id", "?")
        # The freshest SQL lives in the merged subtasks list (sql_generator just wrote it).
        sql = ""
        for st in state.get("subtasks", []) or []:
            if st.get("subtask_id") == subtask_id:
                sql = st.get("generated_sql", "")
                break
        if not sql:
            sql = current.get("generated_sql", "")
        context = state.get("context_config")

        # Safety validation
        result = safety.validate(sql)
        if not result.is_safe:
            logger.warning("SQL safety check failed [%s]: %s", subtask_id, result.reason)
            return {
                "subtasks": [
                    {"subtask_id": subtask_id, "validation_error": result.reason}
                ]
            }

        # Table access check
        if context and hasattr(context, "visible_tables"):
            referenced = set(_TABLE_PATTERN.findall(sql))
            visible = set(context.visible_tables)
            # CTE names declared in this query are not real tables — exclude
            # them from the unauthorized check. The pattern requires `AS (` to
            # be followed by a query keyword so it won't match column aliases.
            cte_names = {n.lower() for n in _CTE_NAME_PATTERN.findall(sql)}
            referenced_lower = {r.lower() for r in referenced}
            visible_lower = {v.lower() for v in visible}
            unauthorized_lower = referenced_lower - visible_lower - cte_names
            unauthorized = {r for r in referenced if r.lower() in unauthorized_lower}
            if unauthorized:
                # Tell the LLM which tables ARE allowed so the retry doesn't
                # just hallucinate a different fake name. Sorted for stability.
                allowed_list = ", ".join(sorted(visible))
                reason = (
                    f"Query references unauthorized tables: {sorted(unauthorized)}. "
                    f"You can ONLY use these tables (any others are forbidden): {allowed_list}. "
                    f"If you need a derived dataset (e.g. completed_orders), build it "
                    f"inline as a CTE or subquery FROM one of the allowed tables — "
                    f"do not reference it as a table name."
                )
                logger.warning("[%s] %s", subtask_id, reason)
                return {
                    "subtasks": [
                        {"subtask_id": subtask_id, "validation_error": reason}
                    ]
                }

        logger.info("SQL validation passed [%s]", subtask_id)
        return {
            "subtasks": [{"subtask_id": subtask_id, "validation_error": None}]
        }

    return _run
