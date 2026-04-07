from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest
from app.harness.orchestrator import Orchestrator

router = APIRouter()


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    orchestrator: Orchestrator = request.app.state.orchestrator
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    return StreamingResponse(
        orchestrator.run_stream(messages, req.context_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
