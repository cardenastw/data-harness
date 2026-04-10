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

    # Token usage — list of {prompt_tokens, completion_tokens} per LLM call.
    # Annotated with `add` so parallel nodes (visualization + strategist) merge
    # via list concatenation instead of raising InvalidUpdateError.
    token_usage: Annotated[list, add]

    # Error
    error: Optional[str]
