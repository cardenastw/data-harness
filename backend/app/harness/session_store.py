from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class Session:
    id: str
    context_id: str
    messages: List[dict] = field(default_factory=list)
    last_query_state: Optional[dict] = field(default=None)
    created_at: datetime = field(default_factory=datetime.now)


class SessionStore:
    """In-memory conversation session store."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}

    def create(self, context_id: str) -> Session:
        session_id = uuid.uuid4().hex
        session = Session(id=session_id, context_id=context_id)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)
