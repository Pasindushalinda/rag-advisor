"""Builds the Market Data agent graph (agent <-> tools)."""

from functools import partial

from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from graphs.market_research_agent import nodes
from graphs.market_research_agent.state import AgentState
from graphs.market_research_agent.tools import TOOLS
from shared.llms import get_chat_model


def build_graph() -> CompiledStateGraph[AgentState]:
    model = get_chat_model().bind_tools(TOOLS)

    g = StateGraph(AgentState)
    g.add_node("agent", partial(nodes.agent, model=model))
    g.add_node("tools", ToolNode(TOOLS, handle_tool_errors=lambda e: f"{type(e).__name__}: {e}"))

    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    return g.compile(name="market_data_agent")
