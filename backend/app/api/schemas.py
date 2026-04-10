from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    context_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    sql: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None
    chart_json: Optional[Dict[str, Any]] = None
    suggestions: List[str] = []
    usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ContextOut(BaseModel):
    id: str
    name: str
    description: str


class ContextsResponse(BaseModel):
    contexts: List[ContextOut]
