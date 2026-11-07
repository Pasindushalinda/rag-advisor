"""The agent node: the AI call that decides which tools to call."""

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel, ConfigDict

from graphs.market_research_agent.state import AgentState

type ToolCaller = Runnable[LanguageModelInput, AIMessage]

AGENT_PROMPT = """You are the Market Data agent for an investment research assistant.
For every listed company or asset (stock, ETF, index, fund, cryptocurrency, currency pair)
the question asks about:
1. Call find_ticker with its name or ticker.
2. Then call get_yahoo_finance_data once with the ticker find_ticker returned.
Make the calls for all companies at once where you can. Do not retry a tool that failed.
When every ticker has its data, reply "done" without calling tools. If the question
names no listed company or asset, call get_general_market_news once, then reply "done"
after receiving the headlines.
Do not answer the question, analyse, or give advice."""


class AgentUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: list[AnyMessage]


def agent(state: AgentState, config: RunnableConfig, *, model: ToolCaller) -> AgentUpdate:
    messages = [SystemMessage(AGENT_PROMPT), HumanMessage(state.question), *state.messages]
    return AgentUpdate(messages=[model.invoke(messages, config)])
