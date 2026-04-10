from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from langgraph.graph import END, StateGraph

from app.graph.nodes.context_gatherer import context_gatherer_node
from app.graph.nodes.docs_answer import docs_answer_node
from app.graph.nodes.docs_lookup import docs_lookup_node
from app.graph.nodes.executor import executor_node
from app.graph.nodes.lineage_answer import lineage_answer_node
from app.graph.nodes.lineage_lookup import lineage_lookup_node
from app.graph.nodes.router import router_node
from app.graph.nodes.sql_generator import sql_generator_node
from app.graph.nodes.strategist import strategist_node
from app.graph.nodes.validator import validator_node
from app.graph.nodes.visualization import visualization_node
from app.graph.state import GraphState

logger = logging.getLogger(__name__)


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


def _route_after_context(state: GraphState) -> Literal["router", "__end__"]:
    if state.get("error"):
        return "__end__"
    return "router"


def _route_after_router(
    state: GraphState,
) -> Literal["sql_generator", "docs_lookup", "lineage_lookup"]:
    qtype = state.get("question_type", "sql")
    if qtype == "docs":
        return "docs_lookup"
    if qtype == "lineage":
        return "lineage_lookup"
    return "sql_generator"


def _route_after_validation(state: GraphState) -> Literal["sql_generator", "executor", "__end__"]:
    if state.get("validation_error"):
        if state.get("sql_attempts", 0) < state.get("_max_retries", 3):
            return "sql_generator"
        return "__end__"
    return "executor"


def _route_after_execution(
    state: GraphState,
) -> Literal["sql_generator", "visualization", "strategist", "__end__"] | list[str]:
    if state.get("execution_error"):
        if state.get("sql_attempts", 0) < state.get("_max_retries", 3):
            return "sql_generator"
        return "__end__"
    if state.get("raw_data"):
        # Fan-out to both analysis nodes in parallel
        return ["visualization", "strategist"]
    return "__end__"


def build_workflow(deps: WorkflowDeps) -> Any:
    graph = StateGraph(GraphState)

    # Existing SQL pipeline nodes (unchanged)
    graph.add_node(
        "context_gatherer",
        context_gatherer_node(deps.sql_engine, deps.context_manager, deps.table_doc_manager),
    )
    graph.add_node("sql_generator", sql_generator_node(deps.llm_client))
    graph.add_node("validator", validator_node(deps.safety))
    graph.add_node(
        "executor",
        executor_node(deps.sql_engine, deps.timeout, deps.max_rows),
    )
    graph.add_node(
        "visualization",
        visualization_node(deps.llm_client, deps.sql_engine, deps.safety, deps.timeout, deps.max_rows),
    )
    graph.add_node("strategist", strategist_node(deps.llm_client))

    # Router + new branches
    graph.add_node("router", router_node(deps.llm_client))
    graph.add_node("docs_lookup", docs_lookup_node(deps.doc_store))
    graph.add_node("docs_answer", docs_answer_node(deps.llm_client))
    graph.add_node("lineage_lookup", lineage_lookup_node(deps.lineage_store))
    graph.add_node("lineage_answer", lineage_answer_node(deps.llm_client))

    # Wire edges
    graph.set_entry_point("context_gatherer")
    graph.add_conditional_edges("context_gatherer", _route_after_context)
    # Router dispatches to one of three subgraphs
    graph.add_conditional_edges("router", _route_after_router)
    # Existing SQL path (unchanged)
    graph.add_edge("sql_generator", "validator")
    graph.add_conditional_edges("validator", _route_after_validation)
    graph.add_conditional_edges("executor", _route_after_execution)
    graph.add_edge("visualization", END)
    graph.add_edge("strategist", END)
    # New paths
    graph.add_edge("docs_lookup", "docs_answer")
    graph.add_edge("docs_answer", END)
    graph.add_edge("lineage_lookup", "lineage_answer")
    graph.add_edge("lineage_answer", END)

    compiled = graph.compile()

    class WorkflowRunner:
        def __init__(self, compiled_graph, max_retries: int):
            self._graph = compiled_graph
            self._max_retries = max_retries

        async def ainvoke(self, state: dict) -> dict:
            state["_max_retries"] = self._max_retries
            return await self._graph.ainvoke(state)

    return WorkflowRunner(compiled, deps.max_sql_retries)
