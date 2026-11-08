"""Multi-turn investor advisor that answers questions, grounded in SEC filings when needed.

Pipeline (LangGraph): plan -> (retrieve x N, market_research ->) answer. plan asks the LLM which
research the latest question needs, with filing research split into one task per company.
route_after_plan then fans out in one step: a retrieve worker per task (each runs the retriever
graph) plus market_research when needed. answer replies from the conversation plus all retrieved
chunks and market data, citing the chunks as [n].
Prompts are pulled from Langfuse (seed them with seed_data/create_investor_advisor_prompts.py).
Served by api/main.py (OpenAI-compatible); the graph is stateless, clients resend the history.
"""

from typing import Annotated, Literal

from langchain_core.callbacks import BaseCallbackManager
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langfuse import Langfuse
from langfuse.decorators import langfuse_context, observe
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict

from graphs.market_research_agent.graph import build_graph as build_market_graph
from graphs.market_research_agent.output import MarketResults, format_market_facts, market_results
from graphs.market_research_agent.state import AgentState as MarketState
from graphs.retriever.retriever import DEFAULT_TOP_K
from graphs.retriever.retriever import State as RetrieverState
from graphs.retriever.retriever import build_graph as build_retriever_graph
from shared.compat import install_shims
from shared.config import get_settings
from shared.models import (
    AdvisorAnswer,
    AdvisorLLMAnswer,
    Citation,
    Plan,
    RetrievalTask,
    RetrievedChunk,
)

install_shims()  # langfuse's LangChain handler imports pre-1.0 langchain paths

from langfuse.callback import CallbackHandler  # needs install_shims() first

TRACE_NAME = "investor_advisor"
PLAN_PROMPT_NAME = "investor_advisor_plan"
ANSWER_PROMPT_NAME = "investor_advisor_answer"
NO_SOURCES = "No filing excerpts were retrieved for this question."


class RetrievalWork(BaseModel):
    """Input of one retrieve worker, sent by route_after_plan; not part of the graph state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn: int
    task_index: int
    task: RetrievalTask


class RetrievalBatch(BaseModel):
    """The chunks one retrieve worker found for its task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn: int
    task_index: int
    company: str
    chunks: list[RetrievedChunk]


class RetrievalUpdate(BaseModel):
    """The only state field a retrieve worker writes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    batches: list[RetrievalBatch]


class MarketUpdate(BaseModel):
    """The only state field market_research writes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market_data: MarketResults


def _latest_turn_batches(
    left: list[RetrievalBatch], right: list[RetrievalBatch]
) -> list[RetrievalBatch]:
    # Parallel retrieve workers each write a batch, so this field needs a reducer. A reducer
    # field cannot be cleared by writing [], and the thread checkpoint keeps earlier turns'
    # batches, so each batch carries its turn and only the newest turn survives. Keyed by
    # (turn, task_index) so re-writing the same batches (answer returns a full state copy)
    # changes nothing, and independent of the order the workers finish in.
    merged = {(b.turn, b.task_index): b for b in [*left, *right]}
    newest = max((turn for turn, _ in merged), default=0)
    return [b for (turn, _), b in sorted(merged.items()) if turn == newest]


class State(BaseModel):
    messages: Annotated[list[AnyMessage], add_messages]
    plan: Plan | None = None
    turn: int = 0
    batches: Annotated[list[RetrievalBatch], _latest_turn_batches] = []
    market_data: MarketResults | None = None
    answer: AdvisorAnswer | None = None


def _chat_model() -> ChatOpenAI:
    cfg = get_settings()
    return ChatOpenAI(
        model=cfg.chat_model_name,
        base_url=cfg.chat_model_endpoint,
        api_key=cfg.chat_model_api_key,  # type: ignore[arg-type]  # str accepted at runtime
        max_retries=6,
        timeout=120,
    )


def _chat_prompt(name: str) -> ChatPromptTemplate:
    lf_prompt = Langfuse().get_prompt(name, label="latest", type="chat")
    # Langfuse v2 chat prompts have no message placeholders, so the seeded prompts are
    # [system, user] and the conversation history is spliced in between them here.
    system, *rest = lf_prompt.get_langchain_prompt()
    prompt = ChatPromptTemplate.from_messages(
        [system, MessagesPlaceholder("history"), *rest]
    )
    # the langfuse_prompt metadata links the generation to the prompt version in Langfuse
    prompt.metadata = {"langfuse_prompt": lf_prompt}
    return prompt


def _split_conversation(messages: list[AnyMessage]) -> tuple[list[AnyMessage], str]:
    *history, last = messages
    if not isinstance(last, HumanMessage):
        raise TypeError("the last message must be the user's question")
    return history, str(last.text)


def _source_label(chunk: RetrievedChunk) -> str:
    return f"{chunk.company} {chunk.form_type} (period ending {chunk.period_end})"


def _format_sources(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return NO_SOURCES
    return "\n\n".join(
        f"[{i}] {_source_label(c)}\n{c.data}" for i, c in enumerate(chunks, start=1)
    )


def _current_chunks(state: State) -> list[RetrievedChunk]:
    """This turn's chunks in task order, without chunks that several tasks found."""
    # Workers finish in any order, so sort by task to keep the [n] numbering stable. The
    # search is unfiltered, so two companies' questions can return the same chunk.
    seen: set[tuple[str, int]] = set()
    chunks: list[RetrievedChunk] = []
    current = sorted(
        (b for b in state.batches if b.turn == state.turn), key=lambda b: b.task_index
    )
    for batch in current:
        for chunk in batch.chunks:
            if (chunk.file, chunk.chunk_num) not in seen:
                seen.add((chunk.file, chunk.chunk_num))
                chunks.append(chunk)
    return chunks


def plan(state: State) -> State:
    history, question = _split_conversation(state.messages)
    llm = _chat_model().with_structured_output(Plan, method="json_schema")
    new_plan = Plan.model_validate(
        (_chat_prompt(PLAN_PROMPT_NAME) | llm).invoke(
            {"history": history, "question": question}
        )
    )
    # market_data/answer persist in the thread checkpoint, so clear the previous turn's values;
    # batches cannot be cleared, so bumping the turn marks the earlier ones as stale
    return state.model_copy(
        update={
            "plan": new_plan,
            "turn": state.turn + 1,
            "market_data": None,
            "answer": None,
        }
    )


def route_after_plan(
    state: State,
) -> list[Send | Literal["market_research", "answer"]]:
    assert state.plan is not None  # set by plan
    targets: list[Send | Literal["market_research", "answer"]] = [
        Send("retrieve", RetrievalWork(turn=state.turn, task_index=i, task=task))
        for i, task in enumerate(state.plan.retrieval_tasks)
    ]
    if state.plan.needs_market_research:
        targets.append("market_research")
    # no research needed (greeting, or a follow-up the conversation already answers)
    return targets or ["answer"]


@observe(name="retrieval", capture_output=False)
def _run_retriever(question: str) -> list[RetrievedChunk]:
    handler = langfuse_context.get_current_langchain_handler()
    result = RetrieverState.model_validate(
        build_retriever_graph().invoke(
            RetrieverState(question=question, top_k=DEFAULT_TOP_K),
            config={"callbacks": [handler]},
        )
    )
    langfuse_context.update_current_observation(
        output={
            "chunks": [(c.file, c.chunk_num, round(c.score, 4)) for c in result.chunks]
        }
    )
    return result.chunks


def _langfuse_parent(config: RunnableConfig) -> tuple[str | None, str | None]:
    """Trace id and current node's span id from the run's Langfuse LangChain handler."""
    manager = config.get("callbacks")
    if not isinstance(manager, BaseCallbackManager):
        return None, None
    for handler in manager.handlers:
        if isinstance(handler, CallbackHandler) and handler.trace is not None:
            span = handler.runs.get(manager.parent_run_id)
            return handler.trace.id, span.id if span is not None else None
    return None, None


def retrieve(state: RetrievalWork, config: RunnableConfig) -> RetrievalUpdate:
    """One worker per RetrievalTask; the workers of a turn run in parallel.

    The parameter is named `state` because LangGraph's node protocol requires it; its type is
    the worker's own input (RetrievalWork), not the graph State.
    """
    # The retriever's nodes use @observe, which would start orphan traces outside an
    # @observe context; parenting the call on this node's span keeps one trace per turn.
    # retrieve() is not reused because it renames the (here: parent) trace to "retrieval".
    trace_id, observation_id = _langfuse_parent(config)
    chunks = _run_retriever(
        state.task.question,
        langfuse_parent_trace_id=trace_id,
        langfuse_parent_observation_id=observation_id,
    )
    batch = RetrievalBatch(
        turn=state.turn,
        task_index=state.task_index,
        company=state.task.company,
        chunks=chunks,
    )
    return RetrievalUpdate(batches=[batch])


def market_research(state: State, config: RunnableConfig) -> MarketUpdate:
    assert state.plan is not None  # set by plan
    # Only reached when plan.needs_market_research (route_after_plan). It runs in the same step
    # as the retrieve workers, so it returns just market_data: a full state copy would also
    # re-write the fields the workers write.
    result = MarketState.model_validate(
        build_market_graph(get_settings()).invoke(
            MarketState(question=state.plan.market_question), config=config
        )
    )
    return MarketUpdate(market_data=market_results(result))


def _format_evidence(state: State, chunks: list[RetrievedChunk]) -> str:
    market = (
        format_market_facts(state.market_data)
        if state.market_data is not None
        else "No current market data was requested."
    )
    return (
        f"SEC filing excerpts:\n{_format_sources(chunks)}\n\nMarket research:\n{market}"
    )


def answer(state: State) -> State:
    history, question = _split_conversation(state.messages)
    chunks = _current_chunks(state)
    llm = _chat_model().with_structured_output(AdvisorLLMAnswer, method="json_schema")
    result = AdvisorLLMAnswer.model_validate(
        (_chat_prompt(ANSWER_PROMPT_NAME) | llm).invoke(
            {
                "history": history,
                "question": question,
                "sources": _format_evidence(state, chunks),
            }
        )
    )
    # Market items have no [n]; the model sometimes lists them in sources, so drop those rather than fail the turn.
    cited = sorted({n for n in result.sources if 1 <= n <= len(chunks)})
    citations = [
        Citation(
            ref=n,
            label=_source_label(chunk),
            file=chunk.file,
            chunk_num=chunk.chunk_num,
        )
        for n in cited
        for chunk in [chunks[n - 1]]
    ]
    advisor_answer = AdvisorAnswer(answer=result.answer, citations=citations)
    content = result.answer
    if citations:
        content += "\n\nSources:\n" + "\n".join(
            f"[{c.ref}] {c.label}" for c in citations
        )
    # add_messages appends this to the thread's history
    return state.model_copy(
        update={"answer": advisor_answer, "messages": [AIMessage(content=content)]}
    )


def build_graph() -> CompiledStateGraph:  # type: ignore[type-arg]  # langgraph generics are unwieldy
    g = StateGraph(State)
    g.add_node("plan", plan)
    g.add_node("retrieve", retrieve, input_schema=RetrievalWork)
    g.add_node("market_research", market_research)
    g.add_node("answer", answer)
    g.add_edge(START, "plan")
    g.add_conditional_edges(
        "plan", route_after_plan, ["retrieve", "market_research", "answer"]
    )
    # retrieve workers and market_research run in the same step, so answer runs once after both
    g.add_edge("retrieve", "answer")
    g.add_edge("market_research", "answer")
    g.add_edge("answer", END)
    return g.compile()


def make_graph(config: RunnableConfig) -> CompiledStateGraph:  # type: ignore[type-arg]  # langgraph generics are unwieldy
    """Graph factory for the LangGraph server, which calls it once per run."""
    get_settings()  # exports LANGFUSE_* from .env into os.environ for the handler
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    user_id = configurable.get("langgraph_auth_user_id")
    # A fresh handler per run: the v2 handler keeps the current trace on the instance,
    # so sharing one across concurrent runs would mix their spans.
    handler = CallbackHandler(
        trace_name=TRACE_NAME,
        session_id=str(thread_id) if thread_id else None,
        user_id=str(user_id) if user_id else None,
        tags=[TRACE_NAME],
    )
    return build_graph().with_config(callbacks=[handler])