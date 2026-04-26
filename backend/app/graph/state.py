from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, Optional, TypedDict


class SubtaskResult(TypedDict, total=False):
    """One subtask the planner emitted, plus its execution output.

    A single user message can produce multiple subtasks. Each is one of four
    types: a SQL query, an investigation (small discovery query the planner
    runs before committing to an answer), a doc lookup, or a lineage lookup.
    The reducer merges updates by `subtask_id`, so the SQL self-correction
    loop overwrites the same slot rather than appending duplicates.
    """

    subtask_id: str  # "s1", "s2", ...
    type: Literal["sql", "investigate", "docs", "lineage"]
    question: str  # planner's per-subtask question
    reason: str  # planner's rationale (used by synthesizer)

    # SQL subtask fields
    generated_sql: str
    raw_data: Optional[dict]  # {columns, rows, row_count, truncated, execution_time_ms}
    chart_json: Optional[dict]
    validation_error: Optional[str]
    execution_error: Optional[str]
    sql_attempts: int

    # Docs subtask fields
    docs_results: Optional[list[dict]]
    docs_answer_text: Optional[str]

    # Lineage subtask fields
    lineage_node: Optional[dict]
    lineage_known: Optional[dict]
    lineage_answer_text: Optional[str]

    # Status
    error: Optional[str]
    completed: bool


def merge_subtasks_by_id(
    left: list[SubtaskResult] | None,
    right: list[SubtaskResult] | None,
) -> list[SubtaskResult]:
    """Reducer: merge subtask updates by id, preserving append order.

    New ids append to the end. Updates to an existing id are shallow-merged
    over the prior entry so SQL retries (which re-enter the same subtask
    multiple times) overwrite per-field rather than duplicating rows.
    """
    if not left and not right:
        return []
    if not left:
        return list(right or [])
    if not right:
        return list(left)

    order: list[str] = []
    merged: dict[str, dict] = {}
    for item in left:
        sid = item.get("subtask_id")
        if sid is None:
            continue
        order.append(sid)
        merged[sid] = dict(item)
    for item in right:
        sid = item.get("subtask_id")
        if sid is None:
            continue
        if sid in merged:
            merged[sid].update(item)
        else:
            order.append(sid)
            merged[sid] = dict(item)
    return [merged[sid] for sid in order]


class GraphState(TypedDict, total=False):
    # Input
    user_question: str
    context_id: str
    session_messages: list[dict]

    # Context phase
    system_prompt: str
    schema_text: str
    context_config: Any  # ContextConfig object

    # Planner output and accumulated results.
    # `subtasks` is the persistent record across both planning rounds — its
    # reducer merges updates by `subtask_id` so retry loops overwrite in place.
    subtasks: Annotated[list[SubtaskResult], merge_subtasks_by_id]
    planning_rounds: int
    ready_to_answer: bool

    # Transient pointer set by Send when fanning out — the current subtask the
    # downstream node should read/write. Has no reducer; downstream nodes must
    # never include it in their return dict, so parallel branches don't fight.
    _current_subtask: Optional[SubtaskResult]

    # Synthesizer output — the user-facing assistant text.
    answer_text: Optional[str]
    suggestions: list[str]

    # Token usage — list of {prompt_tokens, completion_tokens} per LLM call.
    # Annotated with `add` so parallel nodes merge via list concatenation.
    token_usage: Annotated[list, add]

    # Error
    error: Optional[str]
