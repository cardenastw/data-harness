from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class MessageIn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    context_id: str
    messages: List[MessageIn]


class ArtifactOut(BaseModel):
    type: str
    query: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None


class MessageOut(BaseModel):
    role: str = "assistant"
    content: str


class ChatResponse(BaseModel):
    message: MessageOut
    artifacts: List[ArtifactOut] = []


class ContextOut(BaseModel):
    id: str
    name: str
    description: str


class ContextsResponse(BaseModel):
    contexts: List[ContextOut]
