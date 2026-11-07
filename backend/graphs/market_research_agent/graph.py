"""Builds the Market Data agent graph (agent <-> tools)."""

from functools import partial

from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from graphs.market_research_agent import nodes
from graphs.market_research_agent.llm import get_chat_model
from graphs.market_research_agent.state import AgentState
from graphs.market_research_agent.tools import TOOLS
from shared.config import Settings


def build_graph(settings: Settings) -> CompiledStateGraph[AgentState]:
    model = get_chat_model(settings).bind_tools(TOOLS)

    g = StateGraph(AgentState)
    g.add_node("agent", partial(nodes.agent, model=model))
    g.add_node("tools", ToolNode(TOOLS, handle_tool_errors=lambda e: f"{type(e).__name__}: {e}"))

    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    return g.compile(name="market_data_agent")
