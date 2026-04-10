from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.graph.state import GraphState

logger = logging.getLogger(__name__)


ROUTER_SYSTEM_PROMPT = """\
You are a question router for a data analyst assistant. Your job is to read a user's
question and decide which of three tools should answer it.

Tools:
- "sql": numeric data, aggregates, top-N, trends, breakdowns, "how many", "what is
  the total", "show me X by Y". Anything that requires actual values from the
  database.
- "docs": definitions, business rules, policies, glossary entries. "What does X
  mean", "how do we define Y", "what is our policy for Z", "explain the loyalty
  program".
- "lineage": where a metric, table, or column comes from. "Where does X come from",
  "what columns feed metric Y", "how is Z computed", "what table is X from", "what
  is the source system for X".

Return ONLY a JSON object with two fields:
{"type": "sql" | "docs" | "lineage", "subject": "<short subject string>"}

For "docs", `subject` is a search query (keywords).
For "lineage", `subject` is the metric, column, or table name being asked about.
For "sql", `subject` may be empty.

If the question is ambiguous, prefer "sql" — that is the safe default.

Examples:
Q: "What was net revenue last month?"
A: {"type": "sql", "subject": ""}

Q: "What does net revenue mean?"
A: {"type": "docs", "subject": "net revenue definition"}

Q: "Where does customer_lifetime_value come from?"
A: {"type": "lineage", "subject": "customer_lifetime_value"}

Q: "What are the loyalty tier thresholds?"
A: {"type": "docs", "subject": "loyalty tier thresholds"}

Q: "What table is order_type from?"
A: {"type": "lineage", "subject": "orders"}

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


def _parse_routing(raw: str) -> dict:
    """Pull a {type, subject} object out of the LLM response.

    Defends against code fences and trailing prose. Falls back to "sql" on any
    parse failure — the SQL path is the no-regression default.
    """
    text = raw.strip()
    # Strip code fences if present.
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    # Try direct parse, then fall back to extracting the first {...} block.
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        brace_match = re.search(r"\{.*?\}", text, re.DOTALL)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                parsed = None

    if not isinstance(parsed, dict):
        return {"type": "sql", "subject": ""}

    qtype = parsed.get("type")
    if qtype not in ("sql", "docs", "lineage"):
        qtype = "sql"
    subject = parsed.get("subject") or ""
    if not isinstance(subject, str):
        subject = str(subject)

    return {"type": qtype, "subject": subject.strip()}


def router_node(llm_client: Any):
    async def _run(state: GraphState) -> dict:
        if state.get("error"):
            # Context-gatherer already failed; pass through to terminate.
            return {"question_type": "sql", "routing_subject": ""}

        user_question = state["user_question"]

        try:
            response = await llm_client.chat_completion([
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_question},
            ])
            raw = response.choices[0].message.content or ""
            usage = _extract_usage(response)
        except Exception:
            logger.exception("Router LLM call failed; defaulting to sql")
            return {
                "question_type": "sql",
                "routing_subject": "",
                "token_usage": [],
            }

        routing = _parse_routing(raw)
        logger.info("Router classified question as '%s' (subject=%r)", routing["type"], routing["subject"])

        return {
            "question_type": routing["type"],
            "routing_subject": routing["subject"],
            "token_usage": [usage],
        }

    return _run
