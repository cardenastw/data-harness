from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import ChatRequest, ChatResponse
from app.session_store import SessionStore

router = APIRouter()


def _summarize_artifact_for_history(artifact: dict) -> str:
    """One-line compact summary of an artifact, for stuffing into session history.

    The next turn's planner sees these summaries and can decide whether prior
    queries already answered something the user is now asking again.
    """
    sid = artifact.get("subtask_id", "?")
    atype = artifact.get("type", "?")
    q = artifact.get("question", "")
    err = artifact.get("error")
    if err:
        return f"[{sid}] {atype}: {q!r} → ERROR: {err}"
    if atype == "sql":
        sql = (artifact.get("sql") or "").replace("\n", " ").strip()
        raw = artifact.get("raw_data") or {}
        rc = raw.get("row_count", 0) if raw else 0
        cols = raw.get("columns", []) if raw else []
        first = raw.get("rows", [])[:2] if raw else []
        return (
            f"[{sid}] sql: {q!r} → {sql} → {rc} row(s); "
            f"columns={cols}; first_rows={first}"
        )
    if atype == "docs":
        docs = artifact.get("docs") or []
        titles = [d.get("title", "?") for d in docs[:3]]
        return f"[{sid}] docs: {q!r} → matched titles={titles}"
    if atype == "lineage":
        lineage = artifact.get("lineage")
        if lineage:
            return f"[{sid}] lineage: {q!r} → {lineage.get('kind')} {lineage.get('name')}"
        return f"[{sid}] lineage: {q!r} → no record"
    return f"[{sid}] {atype}: {q!r}"


def _build_artifact(subtask: dict) -> dict | None:
    """Project a SubtaskResult into a frontend-facing artifact dict."""
    if not subtask.get("completed"):
        return None
    sid = subtask.get("subtask_id")
    stype = subtask.get("type")
    base: dict = {
        "type": stype,
        "subtask_id": sid,
        "question": subtask.get("question", ""),
        "reason": subtask.get("reason", ""),
    }
    err = (
        subtask.get("error")
        or subtask.get("execution_error")
        or subtask.get("validation_error")
    )
    if err:
        base["error"] = err

    if stype == "sql":
        base["sql"] = subtask.get("generated_sql")
        base["raw_data"] = subtask.get("raw_data")
        base["chart_json"] = subtask.get("chart_json")
    elif stype == "docs":
        base["docs"] = subtask.get("docs_results") or []
        base["answer_text"] = subtask.get("docs_answer_text")
    elif stype == "lineage":
        base["lineage"] = subtask.get("lineage_node")
        base["answer_text"] = subtask.get("lineage_answer_text")

    return base


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    workflow = request.app.state.workflow
    session_store: SessionStore = request.app.state.session_store

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

    session.messages.append({"role": "user", "content": req.message})

    initial_state = {
        "user_question": req.message,
        "context_id": session.context_id,
        "session_messages": session.messages[:-1],
    }

    result = await workflow.ainvoke(initial_state)

    error = result.get("error")
    answer_text = result.get("answer_text")
    subtasks = result.get("subtasks", []) or []
    suggestions = result.get("suggestions", []) or []

    # Build artifact list from completed subtasks.
    artifacts: list[dict] = []
    for st in subtasks:
        artifact = _build_artifact(st)
        if artifact is not None:
            artifacts.append(artifact)

    # Compose the assistant message we persist for next-turn LLM context.
    # We flatten the structured artifacts into a single content string so the
    # planner/sql_generator on the NEXT turn knows what was already fetched.
    if error:
        assistant_content = f"Error: {error}"
    elif answer_text:
        assistant_content = answer_text
    elif artifacts:
        assistant_content = f"Ran {len(artifacts)} subtask(s)."
    else:
        assistant_content = "No results returned."

    if artifacts:
        summaries = [_summarize_artifact_for_history(a) for a in artifacts]
        assistant_content = (
            f"{assistant_content}\n\n[Prior subtasks this turn:\n"
            + "\n".join(summaries)
            + "]"
        )

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
        answer_text=answer_text,
        artifacts=artifacts,
        suggestions=suggestions,
        usage=usage_payload,
        error=error,
    )
