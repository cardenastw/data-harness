from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from app.graph.nodes.context_gatherer import context_gatherer_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.strategist import strategist_node
from app.graph.nodes.subtask_runners import (
    docs_subtask_runner_node,
    lineage_subtask_runner_node,
    sql_subtask_runner_node,
)
from app.graph.nodes.synthesizer import synthesizer_node
from app.graph.state import GraphState, SubtaskResult

logger = logging.getLogger(__name__)


_SUBTASK_TYPE_TO_RUNNER = {
    "sql": "sql_subtask_runner",
    "docs": "docs_subtask_runner",
    "lineage": "lineage_subtask_runner",
}


@dataclass
class WorkflowDeps:
    llm_client: Any
    sql_engine: Any
    safety: Any
    context_manager: Any
    table_doc_manager: Any
    doc_store: Any
    lineage_store: Any
    timeout: float = 30.0
    max_rows: int = 500
    max_sql_retries: int = 3


def _route_after_context(state: GraphState) -> Literal["planner", "__end__"]:
    if state.get("error"):
        return "__end__"
    return "planner"


def _route_after_planner(state: GraphState) -> list[Send] | list[str]:
    """Fan out new (uncompleted) subtasks via Send, one runner per subtask."""
    subtasks: list[SubtaskResult] = list(state.get("subtasks", []) or [])
    pending = [st for st in subtasks if not st.get("completed")]

    if not pending:
        return ["synthesizer", "strategist"]

    sends: list[Send] = []
    for st in pending:
        target = _SUBTASK_TYPE_TO_RUNNER.get(st.get("type", ""))
        if not target:
            continue
        # Send injects _current_subtask into the runner's invocation. The runner
        # threads it through its inner pipeline procedurally.
        payload = {**state, "_current_subtask": st}
        sends.append(Send(target, payload))

    if not sends:
        return ["synthesizer", "strategist"]
    return sends


def _route_after_join(state: GraphState) -> list[str] | str:
    """After all subtasks converge: re-plan (if not ready) or finalize."""
    if state.get("ready_to_answer"):
        return ["synthesizer", "strategist"]
    rounds = state.get("planning_rounds", 0)
    if rounds >= 2:
        return ["synthesizer", "strategist"]
    return "planner"


async def _subtask_join_run(state: GraphState) -> dict:
    """No-op convergence point. All parallel runners terminate here."""
    pending = [
        st for st in state.get("subtasks", []) or []
        if not st.get("completed")
    ]
    if pending:
        ids = [st.get("subtask_id") for st in pending]
        logger.warning("subtask_join reached with pending subtasks: %s", ids)
    return {}


def build_workflow(deps: WorkflowDeps) -> Any:
    graph = StateGraph(GraphState)

    graph.add_node(
        "context_gatherer",
        context_gatherer_node(deps.sql_engine, deps.context_manager, deps.table_doc_manager),
    )
    graph.add_node("planner", planner_node(deps.llm_client))
    graph.add_node(
        "sql_subtask_runner",
        sql_subtask_runner_node(
            deps.llm_client,
            deps.sql_engine,
            deps.safety,
            deps.timeout,
            deps.max_rows,
            max_retries=deps.max_sql_retries,
        ),
    )
    graph.add_node(
        "docs_subtask_runner",
        docs_subtask_runner_node(deps.llm_client, deps.doc_store),
    )
    graph.add_node(
        "lineage_subtask_runner",
        lineage_subtask_runner_node(deps.llm_client, deps.lineage_store),
    )
    graph.add_node("subtask_join", _subtask_join_run)
    graph.add_node("synthesizer", synthesizer_node(deps.llm_client))
    graph.add_node("strategist", strategist_node(deps.llm_client))

    graph.set_entry_point("context_gatherer")
    graph.add_conditional_edges("context_gatherer", _route_after_context)
    graph.add_conditional_edges("planner", _route_after_planner)

    # All subtask runners terminate at the join.
    graph.add_edge("sql_subtask_runner", "subtask_join")
    graph.add_edge("docs_subtask_runner", "subtask_join")
    graph.add_edge("lineage_subtask_runner", "subtask_join")

    graph.add_conditional_edges("subtask_join", _route_after_join)

    graph.add_edge("synthesizer", END)
    graph.add_edge("strategist", END)

    compiled = graph.compile()

    class WorkflowRunner:
        def __init__(self, compiled_graph):
            self._graph = compiled_graph

        async def ainvoke(self, state: dict) -> dict:
            return await self._graph.ainvoke(state)

    return WorkflowRunner(compiled)
