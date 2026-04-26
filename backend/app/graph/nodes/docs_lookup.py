from __future__ import annotations

import logging

from app.context.docs_store import DocStore
from app.graph.state import GraphState

logger = logging.getLogger(__name__)


def docs_lookup_node(doc_store: DocStore):
    async def _run(state: GraphState) -> dict:
        current = state.get("_current_subtask") or {}
        subtask_id = current.get("subtask_id", "?")
        query = current.get("question") or state.get("user_question", "")

        results = doc_store.search(query, limit=3)
        logger.info(
            "Docs lookup [%s] for %r returned %d results", subtask_id, query, len(results)
        )

        results_payload = [
            {
                "path": entry.path,
                "title": entry.title,
                "snippet": entry.snippet(),
                "content": entry.content,
            }
            for entry in results
        ]

        return {
            "subtasks": [
                {"subtask_id": subtask_id, "docs_results": results_payload}
            ]
        }

    return _run
