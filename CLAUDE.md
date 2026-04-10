# AI Harness – Development Guidelines

## Architecture Principles

### LLM owns reasoning, not Python
Do not write Python code (regex classifiers, pattern matchers, hardcoded response generators) to do what the LLM should handle via prompting. If the LLM needs to detect user intent (e.g. "trend query" vs "aggregate query"), put that guidance in the system prompt — not in Python regex.

### Prompt over code for behavior changes
When the LLM produces wrong output (e.g. too-narrow SQL filters), fix it by improving the prompt or context injection — not by adding Python preprocessing that manipulates state before the LLM sees it. Exception: self-correction loops where a subagent retries with error feedback are fine.

### Don't short-circuit the LLM loop
Let the LLM generate its own text responses. Don't return early from the orchestrator with hardcoded summaries or follow-up suggestions. The LLM should loop back after tool execution to produce natural, context-aware responses.

### Multi-turn conversation requires server-side sessions
Every backend that serves the chat API must maintain conversation history server-side. The frontend sends only `session_id` + new message — never the full message array. When building a new backend or rewriting an existing one, carry over the session store pattern from the existing implementation.

### Self-correction loops must include the failed output
When an LLM retry loop feeds an error back to the model (e.g. SQL validation failed, execution error), always include the output that caused the failure alongside the error message. The LLM cannot fix what it cannot see. Pattern: `"Your previous SQL: {failed_sql}\nError: {error}\nWrite a corrected query."` — not just `"Error: {error}"`.

### Carry forward established patterns when building new services
When creating a rewrite or parallel implementation of an existing service, audit the original for patterns that must survive: session management, retry feedback, error handling, streaming contracts, etc. Don't build a "simplified" version that drops load-bearing behavior.

## Project Setup

- Python venv lives at `backend/venv` — use it, don't create new ones
- Docker: `docker compose up` runs backend, frontend, and ollama
- The backend is a LangGraph workflow over Ollama; chat endpoint is `POST /api/chat` (single JSON response, not streaming)
