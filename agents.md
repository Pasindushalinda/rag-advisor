# AGENTS.md

Guidelines for AI agents (and humans) working in this repository.

## Project

A RAG system built from LangGraph agents. Python 3.14, managed with `uv`.
Infrastructure runs locally via `docker compose up -d`:

- **Qdrant**: vector store (`localhost:6333`)
- **Langfuse v2**: observability (`localhost:3000`)

Credentials live in `.env` (never commit it, never print its values).

- **All secrets are passed via `.env`**: API keys, tokens, passwords, and
  hosts/URLs that vary by environment. Never hard-code them, put them in
  source, `docker-compose.yml`, or tests, or pass them as CLI arguments.
- **Keep `.env.example` in sync.** Whenever code requires a new env var, add it
  to `.env.example` in the same change (name only, empty or a safe placeholder
  value, never a real secret). Remove entries that are no longer used.

## Rules

### 1. Tracing: Langfuse for all AI observability

- Every LLM call, retrieval step, and graph run must be traced in Langfuse.
- Attach the Langfuse LangChain `CallbackHandler` to every LangGraph invocation
  (via `config={"callbacks": [handler]}`), so nodes, LLM calls, and tools are
  captured as nested spans of a single trace.
- Use `@observe` for non-LangChain code that is part of an AI flow
  (embedding, chunking, reranking, Qdrant queries).
- Set `session_id`, `user_id`, and descriptive trace names/tags where available.
- Read config from `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
  `LANGFUSE_BASE_URL`. Never hard-code keys or hosts.
- Flush the Langfuse client before short-lived processes exit.

### 2. Agents: LangGraph

- Build all agents and workflows as LangGraph `StateGraph`s.
- One node = one responsibility. Keep nodes small, pure where possible, and
  individually testable.
- Routing decisions are made by deterministic conditional edges that read typed
  state fields, not by parsing prose.
- Compile graphs in one place per agent (`build_graph()`), and keep graph
  wiring separate from node logic.
- Do not call LLMs or tools outside of a graph node.

### 3. Structured output: always

- Every LLM call returns a Pydantic model via
  `llm.with_structured_output(MyModel)` (or the equivalent for the provider).
  Never ask for free-text and regex/`json.loads` it.
- Stay **provider-agnostic**: depend on LangChain chat model interfaces, not on
  a vendor SDK. Model/provider selection comes from config, not code.
- Design output schemas for the code that consumes them: enums or `Literal`s
  for decisions, explicit fields for every value downstream code needs, and
  `Field(description=...)` on each field (descriptions are part of the prompt).
- Only the final user-facing answer may be free text, and even that should be a
  field (e.g. `answer: str`) inside a structured model alongside citations,
  confidence, etc.
- Validate, don't trust: handle `ValidationError` explicitly (retry or fail
  the node) rather than swallowing it.

### 4. Typing: strict, Pydantic everywhere

- All code is fully type-annotated and must pass a strict type checker
  (`mypy --strict`).
- **Use Pydantic models for every data structure**: LLM outputs, tool
  inputs/outputs, graph state, config, Qdrant payloads, API payloads, and
  internal value objects.
- **Do not use** `dict`, `Dict[str, Any]`, `TypedDict`, `NamedTuple`, or bare
  `Any` to represent structured data. If you reach for a dict, define a model.
  Use `dict[K, V]` only for genuine homogeneous mappings (e.g. `dict[DocId, Chunk]`).
- LangGraph state is a Pydantic `BaseModel`, not a `TypedDict`. Nodes take and
  return typed state (or typed partial updates), never raw dicts.
- Prefer `model_config = ConfigDict(frozen=True, extra="forbid")` unless the
  model is deliberately mutable (e.g. graph state).
- Use `Literal`, `Enum`, and `NewType` to make invalid states unrepresentable.
- Settings use `pydantic-settings` (reading from `.env`), loaded once and
  passed in explicitly.
- No `# type: ignore` without a comment explaining why.

## Workflow

- Add dependencies with `uv add <pkg>`; run code with `uv run`.
- Source lives in `src/rag/`.
- **After every implementation, run the linter and type checker and fix all
  errors before considering the work done:**
  - `uv run ruff check --fix . && uv run ruff format .`
  - `uv run mypy --strict src`
  Fix root causes; do not silence errors with `noqa` / `type: ignore` unless
  justified in a comment.
- **Document key decisions as comments.** When code embodies a non-obvious
  decision (a trade-off, a rejected alternative, a constraint, a workaround),
  record the decision and its *why* in a comment next to the code that
  implements it. Don't comment what the code plainly does.
- **Remove outdated decisions when refactoring.** If a change reverses or
  supersedes a decision, delete or rewrite the comment that documented it in the
  same change. Never leave comments describing behavior, constraints, or
  alternatives that no longer apply; stale decision records are worse than none.
- Keep this file under 150 lines. Update it when a rule changes.