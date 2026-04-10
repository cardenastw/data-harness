from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import ChatRequest, ChatResponse
from app.session_store import SessionStore

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    workflow = request.app.state.workflow
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

    # Append user message to session history
    session.messages.append({"role": "user", "content": req.message})

    initial_state = {
        "user_question": req.message,
        "context_id": session.context_id,
        "session_messages": session.messages[:-1],  # prior history (current question sent separately)
    }

    result = await workflow.ainvoke(initial_state)

    # Persist assistant response in session for follow-ups
    sql = result.get("generated_sql")
    raw_data = result.get("raw_data")
    error = result.get("error")
    question_type = result.get("question_type")
    answer_text = result.get("answer_text")
    docs_results = result.get("docs_results")
    lineage_node = result.get("lineage_node")

    if error:
        assistant_content = f"Error: {error}"
    elif answer_text:
        # docs / lineage paths — the LLM already wrote a natural-language answer.
        assistant_content = answer_text
    elif raw_data:
        row_count = raw_data.get("row_count", 0)
        assistant_content = f"Query returned {row_count} row{'s' if row_count != 1 else ''}."
        if sql:
            assistant_content = f"SQL: {sql}\n{assistant_content}"
    else:
        assistant_content = "No results returned."

    session.messages.append({"role": "assistant", "content": assistant_content})

    # Sum per-call usage entries collected by the nodes into a single turn total.
    turn_entries = result.get("token_usage", []) or []
    turn_prompt = sum(e.get("prompt_tokens", 0) for e in turn_entries)
    turn_completion = sum(e.get("completion_tokens", 0) for e in turn_entries)
    turn_usage = {
        "prompt_tokens": turn_prompt,
        "completion_tokens": turn_completion,
        "total_tokens": turn_prompt + turn_completion,
        "llm_calls": len(turn_entries),
    }
    session.accumulate_usage(turn_usage)

    usage_payload = {
        "turn": turn_usage,
        "session": {
            "prompt_tokens": session.total_prompt_tokens,
            "completion_tokens": session.total_completion_tokens,
            "total_tokens": session.total_prompt_tokens + session.total_completion_tokens,
            "llm_calls": session.total_llm_calls,
        },
    }

    return ChatResponse(
        session_id=session.id,
        question_type=question_type,
        sql=sql,
        raw_data=raw_data,
        chart_json=result.get("chart_json"),
        suggestions=result.get("suggestions", []),
        docs_results=docs_results,
        lineage_node=lineage_node,
        answer_text=answer_text,
        usage=usage_payload,
        error=error,
    )
