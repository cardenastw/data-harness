from __future__ import annotations

import logging
from typing import Any

from app.graph.state import GraphState

logger = logging.getLogger(__name__)


DOCS_ANSWER_SYSTEM_PROMPT = """\
You are a data analyst assistant. The user asked a definitional or policy question
and you have been given the most relevant company documentation.

Write a concise answer that:
1. Directly answers the question in 2-4 sentences.
2. Cites the doc title in parentheses, e.g. "(see: Net Revenue)".
3. If the docs do not actually contain the answer, say so plainly — do NOT invent
   facts or make up policies.

Do not mention tools, JSON, or "the documentation says". Just answer naturally with
a citation.
"""


def _extract_usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def _format_docs_for_prompt(results: list[dict]) -> str:
    if not results:
        return "(no documents matched)"
    blocks = []
    for entry in results:
        blocks.append(
            f"## {entry['title']} (file: {entry['path']})\n{entry['content']}"
        )
    return "\n\n---\n\n".join(blocks)


def docs_answer_node(llm_client: Any):
    async def _run(state: GraphState) -> dict:
        current = state.get("_current_subtask") or {}
        subtask_id = current.get("subtask_id", "?")
        question = current.get("question") or state.get("user_question", "")

        # docs_lookup wrote docs_results into the merged subtask list.
        results: list[dict] = []
        for st in state.get("subtasks", []) or []:
            if st.get("subtask_id") == subtask_id:
                results = st.get("docs_results") or []
                break

        if not results:
            return {
                "subtasks": [
                    {
                        "subtask_id": subtask_id,
                        "docs_answer_text": (
                            "I couldn't find documentation matching that question. "
                            "Try rephrasing or asking about a specific term."
                        ),
                        "completed": True,
                    }
                ],
                "token_usage": [],
            }

        docs_text = _format_docs_for_prompt(results)
        user_content = (
            f"User question: {question}\n\n"
            f"Relevant documentation:\n{docs_text}"
        )

        try:
            response = await llm_client.chat_completion([
                {"role": "system", "content": DOCS_ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ])
            usage = _extract_usage(response)
            answer = (response.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("Docs answer LLM call failed [%s]", subtask_id)
            return {
                "subtasks": [
                    {
                        "subtask_id": subtask_id,
                        "docs_answer_text": (
                            "I found relevant documentation but couldn't summarize it. "
                            f"See: {results[0]['title']}."
                        ),
                        "completed": True,
                    }
                ],
                "token_usage": [],
            }

        logger.info("Docs answer [%s] generated (%d chars)", subtask_id, len(answer))
        return {
            "subtasks": [
                {
                    "subtask_id": subtask_id,
                    "docs_answer_text": answer,
                    "completed": True,
                }
            ],
            "token_usage": [usage],
        }

    return _run
