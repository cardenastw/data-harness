from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    context_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    # Routing — set by the router node so the frontend can branch on artifact type.
    question_type: Optional[str] = None  # "sql" | "docs" | "lineage"

    # SQL path
    sql: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None
    chart_json: Optional[Dict[str, Any]] = None
    suggestions: List[str] = []

    # Docs path
    docs_results: Optional[List[Dict[str, Any]]] = None

    # Lineage path
    lineage_node: Optional[Dict[str, Any]] = None

    # Natural-language answer text composed for docs/lineage paths.
    # Empty for SQL path (frontend renders the chart + table instead).
    answer_text: Optional[str] = None

    usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ContextOut(BaseModel):
    id: str
    name: str
    description: str


class ContextsResponse(BaseModel):
    contexts: List[ContextOut]
