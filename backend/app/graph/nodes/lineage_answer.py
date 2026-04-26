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
        current = state.get("_current_subtask") or {}
        subtask_id = current.get("subtask_id", "?")
        question = current.get("question") or state.get("user_question", "")

        # lineage_lookup wrote lineage_node/lineage_known into the merged subtask.
        node = None
        known = None
        for st in state.get("subtasks", []) or []:
            if st.get("subtask_id") == subtask_id:
                node = st.get("lineage_node")
                known = st.get("lineage_known")
                break

        if node is None:
            catalog = json.dumps(known or {}, indent=2)
            user_content = (
                f"User question: {question}\n\n"
                f"No lineage record matched. Known subjects:\n{catalog}"
            )
        else:
            user_content = (
                f"User question: {question}\n\n"
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
            logger.exception("Lineage answer LLM call failed [%s]", subtask_id)
            if node is None:
                fallback = "I couldn't find lineage for that subject."
            else:
                fallback = (
                    f"{node['kind'].title()} {node['name']}: "
                    f"upstream tables {node.get('upstream_tables', [])}."
                )
            return {
                "subtasks": [
                    {
                        "subtask_id": subtask_id,
                        "lineage_answer_text": fallback,
                        "completed": True,
                    }
                ],
                "token_usage": [],
            }

        logger.info("Lineage answer [%s] generated (%d chars)", subtask_id, len(answer))
        return {
            "subtasks": [
                {
                    "subtask_id": subtask_id,
                    "lineage_answer_text": answer,
                    "completed": True,
                }
            ],
            "token_usage": [usage],
        }

    return _run
