import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest
from app.harness.orchestrator import Orchestrator
from app.harness.session_store import SessionStore

router = APIRouter()


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    orchestrator: Orchestrator = request.app.state.orchestrator
    session_store: SessionStore = request.app.state.session_store

    # Look up existing session or create a new one
    if req.session_id:
        session = session_store.get(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        if not req.context_id:
            raise HTTPException(
                status_code=400, detail="context_id required for new conversation"
            )
        session = session_store.create(req.context_id)

    # Append the new user message to session history
    session.messages.append({"role": "user", "content": req.message})
    turn_messages: list[dict] = []
    query_state_out: list = []

    async def stream():
        # Emit session id so the frontend can use it for follow-ups
        yield f"event: session\ndata: {json.dumps({'session_id': session.id})}\n\n"

        async for chunk in orchestrator.run_stream(
            session.messages, session.context_id,
            turn_messages=turn_messages,
            query_state_out=query_state_out,
            prior_query_state=session.last_query_state,
        ):
            yield chunk

        # Persist the canonical messages from this turn (tool calls + results)
        session.messages.extend(turn_messages)
        if query_state_out:
            session.last_query_state = query_state_out[0]

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
