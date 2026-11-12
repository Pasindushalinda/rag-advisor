"""Supervisor-style investor advisor agent.

A `create_agent` loop (model <-> tools) that must call `create_plan` before any other tool, then
gathers evidence with `retrieve_chunks` (SEC filings) and `research_market` (Yahoo Finance) and
answers from it. Each tool runs a sub-graph (planner, retriever, market research).

Tracing: `make_graph` attaches a Langfuse LangChain handler per run, so the model calls, tool calls
and middleware appear as nested spans of one trace. The retriever and planner nodes use `@observe`,
which would start orphan traces inside a LangChain run, so the tools run them through an observed
wrapper parented on the tool's own span (see `_langfuse_parent`).
Served by Aegra (aegra.json), which injects its Postgres checkpointer so a thread keeps its messages
(including tool results) across turns; the plan is reset on every run (see ResetPlanMiddleware).
"""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import NotRequired

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.tools import ToolRuntime, tool
from langchain_core.callbacks import BaseCallbackManager
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langfuse.decorators import langfuse_context, observe
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, Field

from graphs.market_research_agent.graph import build_graph as build_market_graph
from graphs.market_research_agent.output import format_market_facts, market_results
from graphs.market_research_agent.state import AgentState as MarketState
from graphs.planner.planner import State as PlannerState
from graphs.planner.planner import Tool
from graphs.planner.planner import build_graph as build_planner_graph
from graphs.retriever.retriever import DEFAULT_TOP_K
from graphs.retriever.retriever import State as RetrieverState
from graphs.retriever.retriever import build_graph as build_retriever_graph
from shared.compat import install_shims
from shared.config import get_settings
from shared.llms import get_chat_model
from shared.models import RetrievedChunk
from shared.prompts import PromptName, get_prompt

# langfuse's LangChain handler imports pre-1.0 langchain paths, so langfuse.callback is
# imported inside the functions that use it, after this call.
install_shims()

TRACE_NAME = "investor_advisor"
NO_SOURCES = "No filing excerpts were found for this query."


class Step(BaseModel):
    description: str = Field(description="A brief description of the step")
    completed: bool = Field(description="Whether the step has been completed", default=False)


# langchain's create_agent requires a TypedDict state, so this cannot be a Pydantic model;
# the plan entries themselves are Pydantic models.
class InvestorAdvisorState(AgentState):
    plan: NotRequired[list[Step]]


def _langfuse_parent(config: RunnableConfig) -> tuple[str | None, str | None]:
    """Trace id and current run's span id from the run's Langfuse LangChain handler."""
    from langfuse.callback import CallbackHandler

    manager = config.get("callbacks")
    if not isinstance(manager, BaseCallbackManager):
        return None, None
    for handler in manager.handlers:
        if isinstance(handler, CallbackHandler) and handler.trace is not None:
            span = handler.runs.get(manager.parent_run_id)
            return handler.trace.id, span.id if span is not None else None
    return None, None


@contextmanager
def _detached_from_parent_run() -> Iterator[None]:
    """Stop sub-graphs from inheriting the calling tool's run as their parent.

    The @observe wrapper hands them its own Langfuse handler, which has never seen the tool's run id,
    so inheriting it as parent_run_id makes the handler raise KeyError and drop their spans.
    """
    token = var_child_runnable_config.set(None)
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


@observe(name="planner", capture_output=False)
def _run_planner(
    user_request: str, conversation_context: str, available_tools: list[Tool]
) -> PlannerState:
    handler = langfuse_context.get_current_langchain_handler()
    with _detached_from_parent_run():
        result = PlannerState.model_validate(
            build_planner_graph().invoke(
                PlannerState(
                    user_request=user_request,
                    conversation_context=conversation_context,
                    available_tools=available_tools,
                ),
                config={"callbacks": [handler]},
            )
        )
    langfuse_context.update_current_observation(
        output=result.plan.model_dump() if result.plan is not None else None
    )
    return result


@observe(name="retrieval", capture_output=False)
def _run_retriever(question: str) -> list[RetrievedChunk]:
    handler = langfuse_context.get_current_langchain_handler()
    with _detached_from_parent_run():
        result = RetrieverState.model_validate(
            build_retriever_graph().invoke(
                RetrieverState(question=question, top_k=DEFAULT_TOP_K),
                config={"callbacks": [handler]},
            )
        )
    langfuse_context.update_current_observation(
        output={"chunks": [(c.file, c.chunk_num, round(c.score, 4)) for c in result.chunks]}
    )
    return result.chunks


def _format_chunks(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return NO_SOURCES
    return "\n\n".join(
        f"[{i}] {c.company} {c.form_type} (period ending {c.period_end})\n{c.data}"
        for i, c in enumerate(chunks, start=1)
    )


def _format_plan(plan: list[Step]) -> str:
    return "\n".join(f"{i}. [{'x' if s.completed else ' '}] {s.description}" for i, s in enumerate(plan))


@tool
def retrieve_chunks(query: str, runtime: ToolRuntime) -> str:
    """Search SEC filings (10-K/10-Q) for passages relevant to the query.

    Pass a self-contained query that names the company and fiscal period. Returns numbered excerpts.
    """
    trace_id, observation_id = _langfuse_parent(runtime.config)
    chunks = _run_retriever(
        query,
        langfuse_parent_trace_id=trace_id,
        langfuse_parent_observation_id=observation_id,
    )
    return _format_chunks(chunks)


@tool
def research_market(query: str, runtime: ToolRuntime) -> str:
    """Fetch current share prices, financial ratios and recent news for the companies in the query."""
    result = MarketState.model_validate(
        build_market_graph().invoke(MarketState(question=query), config=runtime.config)
    )
    return format_market_facts(market_results(result))


# The planner is told about these tools only; create_plan and the step tracker are not plannable work.
WORK_TOOLS = [retrieve_chunks, research_market]


@tool
def create_plan(user_request: str, conversation_context: str, runtime: ToolRuntime) -> Command:  # type: ignore[type-arg]
    """Create a step-by-step plan for the user's request. Must be called before any other tool.

    user_request: the user's latest request, rewritten to be self-contained (names the company and period).
    conversation_context: the facts and figures already established earlier in the conversation that the
    plan can reuse (e.g. "Apple FY2025 revenue $416.161B and net income $112.010B, from its 10-K").
    Pass an empty string for the first request of a conversation.
    """
    available_tools = [Tool(name=t.name, description=t.description) for t in WORK_TOOLS]
    trace_id, observation_id = _langfuse_parent(runtime.config)
    result = _run_planner(
        user_request,
        conversation_context,
        available_tools,
        langfuse_parent_trace_id=trace_id,
        langfuse_parent_observation_id=observation_id,
    )
    plan = [Step(description=s) for s in (result.plan.steps if result.plan else [])]
    if not plan:
        plan = [Step(description="Answer the user's request")]
    return Command(
        update={
            "plan": plan,
            "messages": [
                ToolMessage(content=f"Plan:\n{_format_plan(plan)}", tool_call_id=runtime.tool_call_id)
            ],
        }
    )


@tool
def update_step_completion_state(step_index: int, completed: bool, runtime: ToolRuntime) -> Command:  # type: ignore[type-arg]
    """Mark a plan step (0-based index) as completed, or incomplete if it needs to be revisited.

    All steps of the plan must be completed to satisfy the user's request.
    """
    plan: list[Step] = runtime.state.get("plan") or []
    if not 0 <= step_index < len(plan):
        message = f"No step {step_index}; the plan has {len(plan)} steps."
        error = ToolMessage(content=message, tool_call_id=runtime.tool_call_id, status="error")
        return Command(update={"messages": [error]})
    updated = [
        s.model_copy(update={"completed": completed}) if i == step_index else s
        for i, s in enumerate(plan)
    ]
    return Command(
        update={
            "plan": updated,
            "messages": [
                ToolMessage(content=f"Plan:\n{_format_plan(updated)}", tool_call_id=runtime.tool_call_id)
            ],
        }
    )


class ResetPlanMiddleware(AgentMiddleware[InvestorAdvisorState, None]):
    """Clears the plan at the start of every run.

    The checkpointer keeps `plan` across the turns of a thread. Without this reset, the previous
    question's plan would satisfy RequirePlanMiddleware and the agent would skip re-planning for a
    follow-up. Messages (and the tool results in them) are kept, so the new plan can reuse that evidence.
    """

    state_schema = InvestorAdvisorState

    def before_agent(self, state: InvestorAdvisorState, runtime: Runtime[None]) -> dict[str, list[Step]]:
        return {"plan": []}

    async def abefore_agent(
        self, state: InvestorAdvisorState, runtime: Runtime[None]
    ) -> dict[str, list[Step]]:
        return {"plan": []}


class RequirePlanMiddleware(AgentMiddleware[InvestorAdvisorState, None]):
    """Rejects any tool call except create_plan while the plan in state is empty."""

    state_schema = InvestorAdvisorState

    @staticmethod
    def _rejection(request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call["name"]
        if name == create_plan.name or request.state.get("plan"):
            return None
        # An error message, not an exception, so the model can recover by creating the plan.
        return ToolMessage(
            content=f"Cannot call '{name}' without a plan. Call '{create_plan.name}' first.",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],  # type: ignore[type-arg]
    ) -> ToolMessage | Command:  # type: ignore[type-arg]
        return self._rejection(request) or handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],  # type: ignore[type-arg]
    ) -> ToolMessage | Command:  # type: ignore[type-arg]
        return self._rejection(request) or await handler(request)


def build_graph() -> CompiledStateGraph:  # type: ignore[type-arg]  # langgraph generics are unwieldy
    return create_agent(
        model=get_chat_model(),
        tools=[create_plan, *WORK_TOOLS, update_step_completion_state],
        system_prompt=get_prompt(PromptName.INVESTOR_ADVISOR_SUPERVISOR).format_messages()[0].text,
        state_schema=InvestorAdvisorState,
        middleware=[
            SummarizationMiddleware(model=get_chat_model(), trigger=("tokens", 4000)),
            ToolCallLimitMiddleware(run_limit=8),  # type: ignore[list-item]  # middleware generics are invariant
            ResetPlanMiddleware(),  # type: ignore[list-item]  # same: state schema subclasses AgentState
            RequirePlanMiddleware(),  # type: ignore[list-item]  # same: state schema subclasses AgentState
        ],
        name=TRACE_NAME,
    )


def make_graph(config: RunnableConfig) -> CompiledStateGraph:  # type: ignore[type-arg]
    """Graph factory for Aegra, called once per run."""
    from langfuse.callback import CallbackHandler

    get_settings()  # exports LANGFUSE_* from .env into os.environ for the handler
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    user_id = configurable.get("user_id")
    # A fresh handler per run: the v2 handler keeps the current trace on the instance,
    # so sharing one across concurrent runs would mix their spans.
    handler = CallbackHandler(
        trace_name=TRACE_NAME,
        session_id=str(thread_id) if thread_id else None,
        user_id=str(user_id) if user_id else None,
        tags=[TRACE_NAME],
    )
    return build_graph().with_config(callbacks=[handler])
