"""HTTP API: OpenAI-compatible chat completions for the investor advisor, plus filing ingestion.

Stateless by design: OpenAI-style clients (Open WebUI, the bundled chat UI, ...) resend the
whole conversation on every request, so the graph runs without a checkpointer.
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langfuse import Langfuse
from pydantic import BaseModel, ConfigDict

from api.openai_models import (
    AssistantMessage,
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatMessage,
    Choice,
    ChunkChoice,
    Delta,
    ModelCard,
    ModelList,
)
from auth import ADMIN_PERMISSION
from graphs.ingestion.ingestion import ingest
from graphs.investor_advisor.investor_advisor import State, make_graph
from graphs.market_research_agent.output import MarketResults
from shared.config import get_settings
from shared.models import IngestionRequest, RetrievalDecision, RetrievedChunk

logger = logging.getLogger(__name__)

MODEL_ID = "investor-advisor"
CHAT_ID_HEADER = (
    "X-OpenWebUI-Chat-Id"  # sent by Open WebUI; used as the Langfuse session
)
FAILURE_TEXT = "Sorry, the request failed. Please try again."


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings()  # exports LANGFUSE_* from .env into os.environ before the client is built
    yield
    Langfuse().flush()


app = FastAPI(lifespan=lifespan)


class NodeUpdate(BaseModel):
    """The state fields a graph node wrote, as emitted by stream_mode="updates"."""

    model_config = ConfigDict(extra="ignore")

    decision: RetrievalDecision | None = None
    chunks: list[RetrievedChunk] | None = None
    market_data: MarketResults | None = None
    messages: list[AnyMessage] | None = None


class Step(BaseModel):
    """A user-visible piece of a run: progress ("reasoning") or the final answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["reasoning", "answer"]
    text: str


def _to_langchain(messages: list[ChatMessage]) -> list[AnyMessage]:
    # The graph owns its system prompts, so client system/tool messages are dropped.
    converted: list[AnyMessage] = []
    for m in messages:
        if m.role == "user":
            converted.append(HumanMessage(content=m.text))
        elif m.role == "assistant":
            converted.append(AIMessage(content=m.text))
    if not converted or not isinstance(converted[-1], HumanMessage):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "the last message must be a user message",
        )
    return converted


def _describe(node: str, update: NodeUpdate) -> Step | None:
    if node == "decide" and update.decision is not None:
        d = update.decision
        label = (
            "Filing lookup needed" if d.needs_retrieval else "No filing lookup needed"
        )
        search = (
            f" Search question: {d.standalone_question}" if d.needs_retrieval else ""
        )
        return Step(kind="reasoning", text=f"{label}. {d.reason}{search}\n\n")
    if node == "retrieve" and update.chunks is not None:
        n = len(update.chunks)
        return Step(
            kind="reasoning",
            text=f"Found {n} filing excerpt{'' if n == 1 else 's'}.\n\n",
        )
    if node == "market_research" and update.market_data is not None:
        n = len(update.market_data.news) + len(update.market_data.facts)
        return Step(
            kind="reasoning",
            text=f"Found {n} current market data item{'' if n == 1 else 's'}.\n\n",
        )
    if node == "answer" and update.messages:
        return Step(kind="answer", text=update.messages[-1].text)
    return None


async def _run(
    messages: list[AnyMessage], user: str | None, chat_id: str | None
) -> AsyncIterator[Step]:
    graph = make_graph(
        {"configurable": {"thread_id": chat_id, "langgraph_auth_user_id": user}}
    )
    async for event in graph.astream(State(messages=messages), stream_mode="updates"):
        for node, values in event.items():
            step = _describe(node, NodeUpdate.model_validate(values))
            if step is not None:
                yield step


def _chunk(
    completion_id: str, created: int, delta: Delta, *, done: bool = False
) -> str:
    chunk = ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=MODEL_ID,
        choices=[ChunkChoice(delta=delta, finish_reason="stop" if done else None)],
    )
    return f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"


async def _stream(
    messages: list[AnyMessage], user: str | None, chat_id: str | None
) -> AsyncIterator[str]:
    completion_id, created = f"chatcmpl-{uuid.uuid4().hex}", int(time.time())
    yield _chunk(completion_id, created, Delta(role="assistant", content=""))
    try:
        async for step in _run(messages, user, chat_id):
            delta = (
                Delta(reasoning_content=step.text)
                if step.kind == "reasoning"
                else Delta(content=step.text)
            )
            yield _chunk(completion_id, created, delta)
    except Exception:
        # Headers are already sent, so report the failure in-band without leaking details.
        logger.exception("investor advisor run failed")
        yield _chunk(completion_id, created, Delta(content=FAILURE_TEXT))
    yield _chunk(completion_id, created, Delta(), done=True)
    yield "data: [DONE]\n\n"


@app.get("/health")
def health() -> Literal["ok"]:
    return "ok"


@app.get("/v1/models")
def list_models() -> ModelList:
    return ModelList(
        data=[
            ModelCard(
                id=MODEL_ID, created=int(time.time()), owned_by="investor-advisor"
            )
        ]
    )


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    request: ChatCompletionRequest,
    user: Annotated[User, Depends(require_auth)],
    chat_id: Annotated[str | None, Header(alias=CHAT_ID_HEADER)] = None,
) -> StreamingResponse | ChatCompletion:
    messages = _to_langchain(request.messages)
    if request.stream:
        return StreamingResponse(
            _stream(messages, user.identity, chat_id), media_type="text/event-stream"
        )
    answer = [
        s.text
        async for s in _run(messages, user.identity, chat_id)
        if s.kind == "answer"
    ]
    return ChatCompletion(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        model=MODEL_ID,
        choices=[Choice(message=AssistantMessage(content="".join(answer)))],
    )


@app.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
def create_ingestion(
    request: IngestionRequest,
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(require_auth)],
) -> dict[str, str]:
    if ADMIN_PERMISSION not in user.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin key required")
    background_tasks.add_task(ingest, request)
    return {"status": "accepted", "ticket": request.ticker}