from fastapi import APIRouter, Request

from app.api.schemas import ContextOut, ContextsResponse
from app.context.manager import ContextManager

router = APIRouter()


@router.get("/contexts", response_model=ContextsResponse)
async def list_contexts(request: Request):
    context_manager: ContextManager = request.app.state.context_manager
    contexts = context_manager.list_all()
    return ContextsResponse(
        contexts=[
            ContextOut(id=c.id, name=c.name, description=c.description)
            for c in contexts
        ]
    )
