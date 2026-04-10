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
        user_question = state["user_question"]
        results = state.get("docs_results") or []

        if not results:
            return {
                "answer_text": (
                    "I couldn't find documentation matching that question. "
                    "Try rephrasing or asking about a specific term."
                ),
                "token_usage": [],
            }

        docs_text = _format_docs_for_prompt(results)
        user_content = (
            f"User question: {user_question}\n\n"
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
            logger.exception("Docs answer LLM call failed")
            return {
                "answer_text": (
                    "I found relevant documentation but couldn't summarize it. "
                    f"See: {results[0]['title']}."
                ),
                "token_usage": [],
            }

        logger.info("Docs answer generated (%d chars)", len(answer))

        return {"answer_text": answer, "token_usage": [usage]}

    return _run
