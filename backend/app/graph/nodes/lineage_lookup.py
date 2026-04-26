from __future__ import annotations

import logging

from app.context.lineage_store import LineageStore
from app.graph.state import GraphState

logger = logging.getLogger(__name__)


def lineage_lookup_node(lineage_store: LineageStore):
    async def _run(state: GraphState) -> dict:
        current = state.get("_current_subtask") or {}
        subtask_id = current.get("subtask_id", "?")
        subject = current.get("question") or state.get("user_question", "")

        node = lineage_store.get(subject)

        if node is None:
            known = lineage_store.list_subjects()
            logger.info("Lineage lookup [%s] miss for %r", subtask_id, subject)
            return {
                "subtasks": [
                    {
                        "subtask_id": subtask_id,
                        "lineage_node": None,
                        "lineage_known": known,
                    }
                ]
            }

        logger.info(
            "Lineage lookup [%s] hit: kind=%s name=%s", subtask_id, node.kind, node.name
        )
        return {
            "subtasks": [
                {
                    "subtask_id": subtask_id,
                    "lineage_node": {
                        "kind": node.kind,
                        "name": node.name,
                        **node.data,
                    },
                    "lineage_known": None,
                }
            ]
        }

    return _run
