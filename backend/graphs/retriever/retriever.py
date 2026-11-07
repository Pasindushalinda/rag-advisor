"""Retrieve SEC filing chunks from Qdrant for a natural-language question.

Pipeline (LangGraph): rewrite_query -> search. The LLM rewrites the question as a self-contained
search query; search embeds it and runs an unfiltered vector search over all filings.
The rewrite prompt is pulled from Langfuse (seed it with seed_data/create_retriever_prompts.py).
Traced in Langfuse as one trace per retrieval with a span per step.
"""

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langfuse import Langfuse
from langfuse.decorators import langfuse_context, observe
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from qdrant_client import QdrantClient

from shared.compat import install_shims
from shared.config import get_settings
from shared.models import QueryRewrite, RetrievalResult, RetrievedChunk

install_shims()  # langfuse's LangChain handler imports pre-1.0 langchain paths

DEFAULT_TOP_K = 5
REWRITE_PROMPT_NAME = "retriever_query_rewrite"


class State(BaseModel):
    question: str
    top_k: int = DEFAULT_TOP_K
    rewrite: QueryRewrite | None = None
    chunks: list[RetrievedChunk] = []


@observe(capture_input=False, capture_output=False)
def rewrite_query(state: State) -> State:
    cfg = get_settings()
    lf_prompt = Langfuse().get_prompt(REWRITE_PROMPT_NAME, label="latest", type="chat")
    # the langfuse_prompt metadata links the generation to the prompt version in Langfuse
    prompt = ChatPromptTemplate.from_messages(lf_prompt.get_langchain_prompt())
    prompt.metadata = {"langfuse_prompt": lf_prompt}
    llm = ChatOpenAI(
        model=cfg.chat_model_name,
        base_url=cfg.chat_model_endpoint,
        api_key=cfg.chat_model_api_key,  # type: ignore[arg-type]  # str accepted at runtime
        max_retries=6,
        timeout=120,
    ).with_structured_output(QueryRewrite, method="json_schema")
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
    embeddings = OpenAIEmbeddings(
        model=cfg.embedding_model_name,
        base_url=cfg.embedding_model_endpoint,
        api_key=cfg.embedding_model_api_key,  # type: ignore[arg-type]  # str accepted at runtime
        check_embedding_ctx_length=False,  # send raw strings; the service does the truncation checks
        max_retries=6,
    )
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
