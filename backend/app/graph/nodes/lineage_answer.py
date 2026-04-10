from __future__ import annotations

import json
import logging
from typing import Any

from app.graph.state import GraphState

logger = logging.getLogger(__name__)


LINEAGE_ANSWER_SYSTEM_PROMPT = """\
You are a data analyst assistant. The user asked a lineage / provenance question
about a metric, table, or column. You have been given the structured lineage record.

Write a concise answer that:
1. States what the subject is (metric / table / column) and its name.
2. Names the upstream tables and columns it depends on.
3. If a formula is provided, mention it briefly.
4. If notes are provided, surface anything that would surprise the user (caveats,
   tax handling, attribution rules, refresh cadence, etc.).
5. Keep it to 3-5 sentences.

If the lineage record is missing, say so plainly and suggest the closest known
subjects from the catalog provided. Do NOT invent lineage that wasn't given.

Do not mention tools, JSON, or "the lineage record". Just answer naturally.
"""


def _extract_usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def lineage_answer_node(llm_client: Any):
    async def _run(state: GraphState) -> dict:
        user_question = state["user_question"]
        node = state.get("lineage_node")
        known = state.get("lineage_known")

        if node is None:
            # Build a fallback with the catalog so the LLM can suggest alternatives.
            catalog = json.dumps(known or {}, indent=2)
            user_content = (
                f"User question: {user_question}\n\n"
                f"No lineage record matched. Known subjects:\n{catalog}"
            )
        else:
            user_content = (
                f"User question: {user_question}\n\n"
                f"Lineage record:\n{json.dumps(node, indent=2, default=str)}"
            )

        try:
            response = await llm_client.chat_completion([
                {"role": "system", "content": LINEAGE_ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ])
            usage = _extract_usage(response)
            answer = (response.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("Lineage answer LLM call failed")
            if node is None:
                return {
                    "answer_text": "I couldn't find lineage for that subject.",
                    "token_usage": [],
                }
            return {
                "answer_text": (
                    f"{node['kind'].title()} {node['name']}: "
                    f"upstream tables {node.get('upstream_tables', [])}."
                ),
                "token_usage": [],
            }

        logger.info("Lineage answer generated (%d chars)", len(answer))
        return {"answer_text": answer, "token_usage": [usage]}

    return _run
