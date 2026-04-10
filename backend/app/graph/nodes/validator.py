from __future__ import annotations

import logging
import re

from app.graph.state import GraphState
from app.sql.safety import SQLSafetyValidator

logger = logging.getLogger(__name__)

_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+[\"'`]?(\w+)[\"'`]?", re.IGNORECASE
)


def validator_node(safety: SQLSafetyValidator):
    async def _run(state: GraphState) -> dict:
        sql = state.get("generated_sql", "")
        context = state.get("context_config")

        # Safety validation
        result = safety.validate(sql)
        if not result.is_safe:
            logger.warning("SQL safety check failed: %s", result.reason)
            return {"validation_error": result.reason}

        # Table access check
        if context and hasattr(context, "visible_tables"):
            referenced = set(_TABLE_PATTERN.findall(sql))
            visible = set(context.visible_tables)
            unauthorized = referenced - visible
            if unauthorized:
                reason = f"Query references unauthorized tables: {unauthorized}"
                logger.warning(reason)
                return {"validation_error": reason}

        logger.info("SQL validation passed")
        return {"validation_error": None}

    return _run
