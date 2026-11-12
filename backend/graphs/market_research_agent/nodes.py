"""The agent node: the AI call that decides which tools to call."""

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel, ConfigDict

from graphs.market_research_agent.state import AgentState
from shared.prompts import PromptName, get_prompt

type ToolCaller = Runnable[LanguageModelInput, AIMessage]

class AgentUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: list[AnyMessage]


def agent(state: AgentState, config: RunnableConfig, *, model: ToolCaller) -> AgentUpdate:
    # the Langfuse SDK caches the prompt, so fetching per call is cheap
    system = get_prompt(PromptName.MARKET_RESEARCH_AGENT).format_messages()[0]
    messages = [system, HumanMessage(state.question), *state.messages]
    return AgentUpdate(messages=[model.invoke(messages, config)])
