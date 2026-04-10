from __future__ import annotations

import logging

from app.context.lineage_store import LineageStore
from app.graph.state import GraphState

logger = logging.getLogger(__name__)


def lineage_lookup_node(lineage_store: LineageStore):
    async def _run(state: GraphState) -> dict:
        subject = state.get("routing_subject", "")
        node = lineage_store.get(subject)

        if node is None:
            # Provide the catalog so the answer node can suggest valid subjects.
            known = lineage_store.list_subjects()
            logger.info("Lineage lookup miss for %r", subject)
            return {
                "lineage_node": None,
                "lineage_known": known,
            }

        logger.info("Lineage lookup hit: kind=%s name=%s", node.kind, node.name)
        return {
            "lineage_node": {
                "kind": node.kind,
                "name": node.name,
                **node.data,
            },
            "lineage_known": None,
        }

    return _run
