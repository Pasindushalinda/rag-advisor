"""Retrieve SEC filing chunks from Qdrant for a natural-language question.

Pipeline (LangGraph): rewrite_query -> search. The LLM rewrites the question as a self-contained
search query; search embeds it and runs an unfiltered vector search over all filings.
The rewrite prompt is pulled from Langfuse (seed it with seed_data/create_retriever_prompts.py).
Traced in Langfuse as one trace per retrieval with a span per step.
"""

from langfuse.decorators import langfuse_context, observe
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from qdrant_client import QdrantClient

from shared.compat import install_shims
from shared.config import get_settings
from shared.llms import get_chat_model, get_embedding_model
from shared.models import QueryRewrite, RetrievalResult, RetrievedChunk
from shared.prompts import PromptName, get_prompt

install_shims()  # langfuse's LangChain handler imports pre-1.0 langchain paths

DEFAULT_TOP_K = 5


class State(BaseModel):
    question: str
    top_k: int = DEFAULT_TOP_K
    rewrite: QueryRewrite | None = None
    chunks: list[RetrievedChunk] = []


@observe(capture_input=False, capture_output=False)
def rewrite_query(state: State) -> State:
    prompt = get_prompt(PromptName.RETRIEVER_QUERY_REWRITE)
    llm = get_chat_model().with_structured_output(QueryRewrite, method="json_schema")
    rewrite = QueryRewrite.model_validate(
        (prompt | llm).invoke({"question": state.question})
    )
    langfuse_context.update_current_observation(
        input={"question": state.question}, output=rewrite.model_dump()
    )
    return state.model_copy(update={"rewrite": rewrite})


@observe(capture_input=False, capture_output=False)
def search(state: State) -> State:
    cfg = get_settings()
    rewrite = state.rewrite
    assert rewrite is not None  # set by rewrite_query
    embeddings = get_embedding_model()
    vector = embeddings.embed_query(rewrite.search_query)

    client = QdrantClient(
        url=cfg.qdrant_url,
        # Without port=None the client appends :6333 to portless (hosted https) URLs.
        port=None,
        timeout=cfg.qdrant_timeout,
        api_key=cfg.qdrant_api_key.get_secret_value() if cfg.qdrant_api_key else None,
    )
    response = client.query_points(
        cfg.qdrant_collection,
        query=vector,
        limit=state.top_k,
        with_payload=True,
    )
    chunks = [
        RetrievedChunk.model_validate({**(p.payload or {}), "score": p.score})
        for p in response.points
    ]
    langfuse_context.update_current_observation(
        input={"query": rewrite.search_query, "top_k": state.top_k},
        output={"chunks": [(c.file, c.chunk_num, round(c.score, 4)) for c in chunks]},
    )
    return state.model_copy(update={"chunks": chunks})


def build_graph() -> CompiledStateGraph:  # type: ignore[type-arg]  # langgraph generics are unwieldy
    g = StateGraph(State)
    g.add_node("rewrite_query", rewrite_query)
    g.add_node("search", search)
    g.add_edge(START, "rewrite_query")
    g.add_edge("rewrite_query", "search")
    g.add_edge("search", END)
    return g.compile()


@observe(name="retrieval", capture_output=False)
def retrieve(question: str, top_k: int = DEFAULT_TOP_K) -> RetrievalResult:
    """Run one retrieval for a question; returns the rewritten query and the top_k chunks."""
    langfuse_context.update_current_trace(
        name="retrieval",
        input={"question": question, "top_k": top_k},
        tags=["retrieval"],
    )
    handler = langfuse_context.get_current_langchain_handler()
    result = State.model_validate(
        build_graph().invoke(
            State(question=question, top_k=top_k), config={"callbacks": [handler]}
        )
    )
    assert result.rewrite is not None
    langfuse_context.update_current_observation(
        output={"rewrite": result.rewrite.model_dump(), "chunks": len(result.chunks)}
    )
    return RetrievalResult(
        question=question, rewrite=result.rewrite, chunks=result.chunks
    )
