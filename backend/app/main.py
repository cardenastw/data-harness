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
    from app.harness.sql.sqlite_engine import SQLiteEngine

    sql_engine = SQLiteEngine(settings.database_path)
    await sql_engine.initialize()

    # Load contexts
    from app.harness.context_manager import ContextManager

    context_manager = ContextManager(contexts_dir=Path(settings.contexts_dir))
    context_manager.load_all()

    # Load table documentation
    from app.harness.table_docs import TableDocManager

    table_doc_manager = TableDocManager(tables_dir=Path(settings.tables_dir))
    table_doc_manager.load_all()

    # Register tools
    from app.harness.sql.safety import SQLSafetyValidator
    from app.harness.tool_registry import ToolRegistry
    from app.harness.tools.get_schema import GetSchemaTool
    from app.harness.tools.run_sql import RunSQLTool

    sql_safety = SQLSafetyValidator()
    tool_registry = ToolRegistry()
    tool_registry.register(GetSchemaTool(sql_engine, table_doc_manager))
    tool_registry.register(RunSQLTool(sql_engine, sql_safety, settings))

    # Create orchestrator
    from app.harness.llm.client import LLMClient
    from app.harness.orchestrator import Orchestrator
    from app.harness.prompt_builder import PromptBuilder
    from app.harness.tool_executor import ToolExecutor

    llm_client = LLMClient(base_url=settings.ollama_base_url, model=settings.model_name)

    orchestrator = Orchestrator(
        llm_client=llm_client,
        tool_registry=tool_registry,
        tool_executor=ToolExecutor(tool_registry),
        context_manager=context_manager,
        prompt_builder=PromptBuilder(table_doc_manager),
        sql_engine=sql_engine,
        sql_safety_validator=sql_safety,
        settings=settings,
    )

    app.state.orchestrator = orchestrator
    app.state.context_manager = context_manager

    yield

    await sql_engine.close()


app = FastAPI(title="AI Data Harness", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include routes
from app.api.routes.chat import router as chat_router
from app.api.routes.contexts import router as contexts_router

app.include_router(chat_router, prefix="/api")
app.include_router(contexts_router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
