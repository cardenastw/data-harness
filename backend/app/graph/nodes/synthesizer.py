from __future__ import annotations

import json
import logging
from typing import Any

from app.graph.state import GraphState, SubtaskResult

logger = logging.getLogger(__name__)


SYNTHESIZER_SYSTEM_PROMPT = """\
You are writing the final answer to the user's question, given the results of
the subtasks below. Write a single coherent natural-language answer.

CRITICAL RULES — these are non-negotiable:
1. NEVER output placeholder text like "[amount from s1]", "[query result]",
   "[X]", "(see result above)", or any bracketed/parenthesized stand-in for a
   number. If you don't have a number, do not write a sentence that needs one.
2. If a SQL subtask FAILED (status shows "FAILED:"), do not pretend to have its
   number. Either omit the topic from your answer entirely, or say plainly that
   the query couldn't be run.
3. Only cite numbers that literally appear in the subtask `result` block.

Other rules:
- For docs: weave the definition / policy into your answer; reference doc titles
  in parentheses when relevant.
- For lineage: state plainly where the metric/column comes from.
- Do not show SQL code or technical implementation details — those render
  separately as artifacts.
- Keep it concise: 2-4 sentences for simple cases, 1-2 short paragraphs when
  multiple subtasks contribute.
- Do not say "based on the data" or "according to the results" — just answer.
"""


def _extract_usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def _format_subtask(st: SubtaskResult, index: int) -> str:
    """Render one subtask as plain prose for the synthesizer.

    Avoids subtask-id labels (e.g. "[s1]") because the model can latch onto
    them and emit placeholders like "[amount from s1]" in its answer.
    """
    stype = st.get("type", "?")
    q = st.get("question", "")
    header = f"Subtask {index} ({stype}) — asked: {q!r}"

    err = st.get("error") or st.get("execution_error") or st.get("validation_error")
    if err:
        return (
            f"{header}\n"
            f"  STATUS: FAILED — could not produce a result.\n"
            f"  Error: {err}\n"
            f"  Do NOT fabricate a number for this subtask in your answer."
        )

    if stype == "sql":
        raw = st.get("raw_data") or {}
        cols = raw.get("columns", [])
        rows = raw.get("rows", [])
        rc = raw.get("row_count", 0)
        first = rows[:3] if rows else []
        return (
            f"{header}\n"
            f"  STATUS: OK — {rc} row(s) returned.\n"
            f"  Columns: {cols}\n"
            f"  Data (first up to 3 rows): {first}"
        )

    if stype == "docs":
        docs = st.get("docs_results") or []
        titles = [d.get("title", "?") for d in docs]
        snippets = [d.get("snippet", "")[:200] for d in docs[:3]]
        answer = st.get("docs_answer_text") or ""
        return (
            f"{header}\n"
            f"  STATUS: OK\n"
            f"  Matched docs: {titles}\n"
            f"  Snippets: {snippets}\n"
            f"  Doc summary already drafted: {answer}"
        )

    if stype == "lineage":
        node = st.get("lineage_node")
        prior = st.get("lineage_answer_text") or ""
        if node:
            return (
                f"{header}\n"
                f"  STATUS: OK\n"
                f"  Lineage record: {json.dumps(node, default=str)[:1000]}\n"
                f"  Lineage summary already drafted: {prior}"
            )
        known = st.get("lineage_known")
        return (
            f"{header}\n"
            f"  STATUS: NO RECORD FOUND\n"
            f"  Known subjects in catalog: {json.dumps(known, default=str)[:500]}"
        )

    return header


def synthesizer_node(llm_client: Any):
    async def _run(state: GraphState) -> dict:
        if state.get("error"):
            return {"answer_text": f"Error: {state['error']}", "token_usage": []}

        user_question = state["user_question"]
        subtasks: list[SubtaskResult] = list(state.get("subtasks", []) or [])

        # Investigations are scaffolding (small discovery queries the planner
        # ran to inform the answer). They are never shown to the user.
        answer_subtasks = [
            st for st in subtasks if st.get("type") != "investigate"
        ]

        if not answer_subtasks:
            return {
                "answer_text": "I couldn't run any subtasks for that question.",
                "token_usage": [],
            }

        formatted = "\n\n".join(
            _format_subtask(st, i + 1) for i, st in enumerate(answer_subtasks)
        )
        user_content = (
            f"User question: {user_question}\n\n"
            f"Subtask results:\n{formatted}"
        )

        try:
            response = await llm_client.chat_completion([
                {"role": "system", "content": SYNTHESIZER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ])
            usage = _extract_usage(response)
            answer = (response.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("Synthesizer LLM call failed")
            # Fallback: short, factual summary of what ran.
            n_ok = sum(1 for s in answer_subtasks if s.get("completed") and not s.get("error"))
            n_fail = sum(1 for s in answer_subtasks if s.get("error") or s.get("execution_error"))
            return {
                "answer_text": (
                    f"Ran {len(answer_subtasks)} subtask(s) — {n_ok} succeeded, {n_fail} failed. "
                    f"See artifacts below for details."
                ),
                "token_usage": [],
            }

        logger.info("Synthesizer composed answer (%d chars)", len(answer))
        return {"answer_text": answer, "token_usage": [usage]}

    return _run
