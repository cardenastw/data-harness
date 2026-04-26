from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.graph.state import GraphState, SubtaskResult

logger = logging.getLogger(__name__)


PLANNER_SYSTEM_PROMPT = """\
You are a planner for a data analyst assistant. Read the user's question and any
results from prior subtasks, then decide what to do next.

You can call three kinds of tools:
- "sql": numeric data, aggregates, top-N, trends, breakdowns.
- "docs": definitions, business rules, policies, glossary entries.
- "lineage": where a metric/column/table comes from.

Return ONLY a JSON object:
{
  "reasoning": "<one sentence on what the user wants and your plan>",
  "ready_to_answer": true | false,
  "new_subtasks": [
    {"type": "sql" | "docs" | "lineage", "question": "...", "reason": "..."}
  ]
}

Rules:
- Prefer ONE subtask. Only split when the questions are about genuinely different
  things — different metrics, different time ranges, OR mixing types (sql + docs).
- Do NOT split a single SQL question into multiple SQL subtasks just because the
  query has multiple SELECT columns. One query that returns "revenue and orders"
  by month is BETTER than two queries.
- Combine SQL and docs in one plan when the user asks both for a number AND its
  definition (e.g. "what was net revenue last month and how do we define it").
- If the prior round's results already answer the question, set
  ready_to_answer=true and new_subtasks=[].
- After round 2, you cannot plan more — set ready_to_answer=true.
- Hard cap: at most 4 subtasks total across all rounds.

Examples:

User: "What was revenue last month?"
{"reasoning": "Single SQL aggregate.", "ready_to_answer": true, "new_subtasks": [
  {"type": "sql", "question": "Revenue last month", "reason": "Aggregate over orders."}
]}

User: "Show me revenue and orders by month"
{"reasoning": "One query produces both columns.", "ready_to_answer": true, "new_subtasks": [
  {"type": "sql", "question": "Revenue and order count by month", "reason": "Two aggregates per month bucket — single query."}
]}

User: "What was net revenue last month and what does net revenue mean?"
{"reasoning": "Number + definition. SQL + docs in parallel.", "ready_to_answer": true, "new_subtasks": [
  {"type": "sql", "question": "Net revenue last month", "reason": "Aggregate."},
  {"type": "docs", "question": "net revenue definition", "reason": "Need the business definition."}
]}

User: "Show me revenue trend AND top customers"
{"reasoning": "Two unrelated questions — different shapes.", "ready_to_answer": true, "new_subtasks": [
  {"type": "sql", "question": "Revenue trend over time", "reason": "Time series."},
  {"type": "sql", "question": "Top customers by revenue", "reason": "Ranked list — different grain."}
]}

Return ONLY the JSON object. No code fences, no commentary.
"""


def _extract_usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def _parse_plan(raw: str) -> dict:
    """Extract {reasoning, ready_to_answer, new_subtasks} from LLM output.

    Defends against code fences and trailing prose. On parse failure falls
    back to a single SQL subtask using the user's raw question — same
    no-regression default the previous router had.
    """
    text = raw.strip()
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    parsed: Any = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                parsed = None

    if not isinstance(parsed, dict):
        return {"reasoning": "fallback", "ready_to_answer": True, "new_subtasks": []}

    new_subtasks_raw = parsed.get("new_subtasks") or []
    if not isinstance(new_subtasks_raw, list):
        new_subtasks_raw = []

    cleaned: list[dict] = []
    for entry in new_subtasks_raw:
        if not isinstance(entry, dict):
            continue
        stype = entry.get("type")
        if stype not in ("sql", "docs", "lineage"):
            continue
        question = (entry.get("question") or "").strip()
        if not question:
            continue
        reason = (entry.get("reason") or "").strip()
        cleaned.append({"type": stype, "question": question, "reason": reason})

    return {
        "reasoning": str(parsed.get("reasoning") or "")[:500],
        "ready_to_answer": bool(parsed.get("ready_to_answer", False)),
        "new_subtasks": cleaned,
    }


def _summarize_completed_subtasks(subtasks: list[SubtaskResult]) -> str:
    """Render completed subtasks compactly for the round-2 planner prompt."""
    if not subtasks:
        return ""
    lines: list[str] = []
    for st in subtasks:
        if not st.get("completed"):
            continue
        sid = st.get("subtask_id", "?")
        stype = st.get("type", "?")
        q = st.get("question", "")
        if stype == "sql":
            raw = st.get("raw_data") or {}
            rc = raw.get("row_count", 0) if raw else 0
            cols = raw.get("columns", []) if raw else []
            preview = raw.get("rows", [])[:2] if raw else []
            err = st.get("error") or st.get("execution_error") or st.get("validation_error")
            if err:
                lines.append(f"- [{sid}] sql: {q!r} → ERROR: {err}")
            else:
                lines.append(
                    f"- [{sid}] sql: {q!r} → {rc} row(s); columns={cols}; first={preview}"
                )
        elif stype == "docs":
            docs = st.get("docs_results") or []
            titles = [d.get("title", "?") for d in docs[:3]]
            answer = (st.get("docs_answer_text") or "")[:200]
            lines.append(f"- [{sid}] docs: {q!r} → matched {titles}; answer={answer!r}")
        elif stype == "lineage":
            node = st.get("lineage_node")
            if node:
                lines.append(f"- [{sid}] lineage: {q!r} → {node.get('kind')} {node.get('name')}")
            else:
                lines.append(f"- [{sid}] lineage: {q!r} → no record")
    return "\n".join(lines)


MAX_PLANNING_ROUNDS = 2
MAX_SUBTASKS_PER_TURN = 4


def planner_node(llm_client: Any):
    async def _run(state: GraphState) -> dict:
        if state.get("error"):
            # Context-gatherer already failed; pass through.
            return {
                "ready_to_answer": True,
                "planning_rounds": state.get("planning_rounds", 0) + 1,
                "subtasks": [],
                "token_usage": [],
            }

        user_question = state["user_question"]
        existing_subtasks: list[SubtaskResult] = list(state.get("subtasks", []) or [])
        round_index = state.get("planning_rounds", 0)

        # Hard cap: at round 2 (the third call would be round_index=2), don't plan more.
        if round_index >= MAX_PLANNING_ROUNDS:
            logger.info("Planner cap reached (rounds=%d); forcing answer", round_index)
            return {
                "ready_to_answer": True,
                "planning_rounds": round_index + 1,
                "subtasks": [],
                "token_usage": [],
            }

        # Build user prompt — include prior subtask summary on round 2+.
        user_parts = [f"User question: {user_question}"]
        completed_summary = _summarize_completed_subtasks(existing_subtasks)
        if completed_summary:
            user_parts.append(
                f"\nResults from prior subtasks:\n{completed_summary}\n\n"
                f"Decide whether these results already answer the question. "
                f"If yes, set ready_to_answer=true and new_subtasks=[]. "
                f"If you need ONE more lookup, emit it now — this is your last round."
            )
        user_content = "\n".join(user_parts)

        # Include prior-turn session history so the planner can avoid re-querying
        # data that was already fetched and cited in a previous turn.
        session_messages = state.get("session_messages", []) or []
        messages = [{"role": "system", "content": PLANNER_SYSTEM_PROMPT}]
        messages.extend(session_messages)
        messages.append({"role": "user", "content": user_content})

        try:
            response = await llm_client.chat_completion(messages)
            raw = response.choices[0].message.content or ""
            usage = _extract_usage(response)
        except Exception:
            logger.exception("Planner LLM call failed; falling back to single SQL subtask")
            fallback_id = f"s{len(existing_subtasks) + 1}"
            return {
                "subtasks": [
                    {
                        "subtask_id": fallback_id,
                        "type": "sql",
                        "question": user_question,
                        "reason": "fallback after planner error",
                        "sql_attempts": 0,
                        "completed": False,
                    }
                ],
                "ready_to_answer": True,
                "planning_rounds": round_index + 1,
                "token_usage": [],
            }

        plan = _parse_plan(raw)
        logger.info(
            "Planner round %d: %d new subtask(s); ready_to_answer=%s",
            round_index + 1,
            len(plan["new_subtasks"]),
            plan["ready_to_answer"],
        )

        # Cap: at most MAX_SUBTASKS_PER_TURN total across all rounds.
        remaining_slots = MAX_SUBTASKS_PER_TURN - len(existing_subtasks)
        if remaining_slots <= 0:
            new_subtasks_capped: list[dict] = []
            ready = True
        else:
            new_subtasks_capped = plan["new_subtasks"][:remaining_slots]
            ready = plan["ready_to_answer"]
            if len(plan["new_subtasks"]) > remaining_slots:
                logger.info(
                    "Trimmed %d subtasks to fit %d remaining slots",
                    len(plan["new_subtasks"]),
                    remaining_slots,
                )
                ready = True  # don't loop again if we were already trimmed

        # Assign fresh ids: s{N+1}, s{N+2}, ...
        next_index = len(existing_subtasks) + 1
        new_subtask_records: list[SubtaskResult] = []
        for i, entry in enumerate(new_subtasks_capped):
            new_subtask_records.append({
                "subtask_id": f"s{next_index + i}",
                "type": entry["type"],
                "question": entry["question"],
                "reason": entry["reason"],
                "sql_attempts": 0,
                "completed": False,
            })

        # If LLM said ready but there are NO new subtasks AND no existing ones,
        # something's off — fall back to a single SQL subtask on round 1 only.
        if (
            not new_subtask_records
            and not existing_subtasks
            and round_index == 0
        ):
            logger.warning("Planner emitted no subtasks on round 1; falling back to single SQL subtask")
            new_subtask_records.append({
                "subtask_id": "s1",
                "type": "sql",
                "question": user_question,
                "reason": "fallback: planner returned empty plan",
                "sql_attempts": 0,
                "completed": False,
            })
            ready = True

        return {
            "subtasks": new_subtask_records,
            "ready_to_answer": ready,
            "planning_rounds": round_index + 1,
            "token_usage": [usage],
        }

    return _run
