from __future__ import annotations

from operator import add
from typing import Annotated, Any, Optional, TypedDict


class GraphState(TypedDict, total=False):
    # Input
    user_question: str
    context_id: str
    session_messages: list[dict]

    # Context phase
    system_prompt: str
    schema_text: str
    context_config: Any  # ContextConfig object

    # SQL generation / validation loop
    generated_sql: str
    validation_error: Optional[str]
    execution_error: Optional[str]
    sql_attempts: int

    # Execution results
    raw_data: Optional[dict]  # {columns, rows, row_count, truncated, execution_time_ms}

    # Analysis outputs
    chart_json: Optional[dict]  # Recharts-compatible chart config
    suggestions: list[str]  # Follow-up question suggestions

    # Routing — set by router_node, drives the conditional edge after the router.
    # "sql" (default) routes through the existing SQL pipeline. "docs" and
    # "lineage" route through the new lookup+answer subgraphs.
    question_type: str  # "sql" | "docs" | "lineage"
    routing_subject: str  # search query for docs / canonical name for lineage

    # Docs path outputs
    docs_results: Optional[list[dict]]  # [{path, title, snippet, content}]

    # Lineage path outputs
    lineage_node: Optional[dict]  # {kind, name, formula?, upstream_tables?, ...}
    lineage_known: Optional[dict]  # {metrics: [...], columns: [...], tables: [...]} on miss

    # Natural-language answer composed by docs_answer / lineage_answer nodes.
    # SQL path leaves this empty — the chat route falls back to its row-count text.
    answer_text: Optional[str]

    # Token usage — list of {prompt_tokens, completion_tokens} per LLM call.
    # Annotated with `add` so parallel nodes (visualization + strategist) merge
    # via list concatenation instead of raising InvalidUpdateError.
    token_usage: Annotated[list, add]

    # Error
    error: Optional[str]
