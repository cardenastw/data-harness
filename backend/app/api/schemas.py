from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    context_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str

    # The synthesized natural-language answer. Composed by the synthesizer node
    # from all subtask results.
    answer_text: Optional[str] = None

    # Structured artifacts — one entry per completed subtask. Each entry has at
    # minimum {type, subtask_id, question, reason} plus type-specific fields:
    #   sql:     sql, raw_data, chart_json, error?
    #   docs:    docs (list of {path, title, snippet, content}), answer_text, error?
    #   lineage: lineage, answer_text, error?
    artifacts: List[Dict[str, Any]] = []

    # Cross-cutting follow-up questions across the whole turn.
    suggestions: List[str] = []

    usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ContextOut(BaseModel):
    id: str
    name: str
    description: str


class ContextsResponse(BaseModel):
    contexts: List[ContextOut]
