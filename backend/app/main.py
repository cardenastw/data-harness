import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Initialize SQL engine
    from app.sql.sqlite_engine import SQLiteEngine

    sql_engine = SQLiteEngine(settings.database_path)
    await sql_engine.initialize()

    # Load contexts
    from app.context.manager import ContextManager

    context_manager = ContextManager(contexts_dir=Path(settings.contexts_dir))
    context_manager.load_all()

    # Load table docs
    from app.context.table_docs import TableDocManager

    table_doc_manager = TableDocManager(tables_dir=Path(settings.tables_dir))
    table_doc_manager.load_all()

    # Safety validator
    from app.sql.safety import SQLSafetyValidator

    safety = SQLSafetyValidator()

    # LLM client (OpenAI-compatible, pointed at Ollama)
    from openai import AsyncOpenAI

    class LLMClient:
        def __init__(self, base_url: str, model: str):
            self._client = AsyncOpenAI(base_url=base_url, api_key="ollama")
            self._model = model

        async def chat_completion(self, messages, **kwargs):
            return await self._client.chat.completions.create(
                model=self._model, messages=messages, **kwargs,
            )

    llm_client = LLMClient(base_url=settings.ollama_base_url, model=settings.model_name)

    # Build and compile the LangGraph workflow
    from app.graph.workflow import WorkflowDeps, build_workflow

    deps = WorkflowDeps(
        llm_client=llm_client,
        sql_engine=sql_engine,
        safety=safety,
        context_manager=context_manager,
        table_doc_manager=table_doc_manager,
        timeout=settings.sql_query_timeout,
        max_rows=settings.sql_max_rows,
        max_sql_retries=settings.max_sql_retries,
    )

    workflow = build_workflow(deps)

    from app.session_store import SessionStore

    session_store = SessionStore()

    app.state.workflow = workflow
    app.state.context_manager = context_manager
    app.state.session_store = session_store

    yield

    await sql_engine.close()


app = FastAPI(title="AI Data Harness", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api.routes.chat import router as chat_router
from app.api.routes.contexts import router as contexts_router

app.include_router(chat_router, prefix="/api")
app.include_router(contexts_router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
