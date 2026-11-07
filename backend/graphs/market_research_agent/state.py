"""The graph state passed between nodes."""

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class AgentState(BaseModel):
    question: str
    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)
