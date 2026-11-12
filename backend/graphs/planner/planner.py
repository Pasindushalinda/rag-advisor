from langfuse.decorators import langfuse_context, observe
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from shared.llms import get_chat_model
from shared.prompts import PromptName, get_prompt


class Plan(BaseModel):
    steps: list[str] = Field(
        description="Step by step instructions to be executed",
        default=[],
    )


class Tool(BaseModel):
    name: str = Field(description="The name of the tool")
    description: str = Field(description="A brief description of the tool")


class State(BaseModel):
    user_request: str = Field(description="The request from the user which needs to be addressed by the plan")
    conversation_context: str = Field(
        description="Facts and results from earlier in the conversation that the plan can build on",
        default="",
    )
    plan: Plan | None = Field(description="The plan to solve the problem", default=None)
    available_tools: list[Tool] = Field(
        description="The tools available to satisfy the user request", default=[]
    )


@observe(capture_input=False, capture_output=False)
def planner(state: State) -> State:
    llm = get_chat_model().with_structured_output(Plan, method="json_schema")

    prompt = get_prompt(PromptName.PLANNER_PLAN_STEPS)

    available_tools = "\n".join(f"- {t.name}: {t.description}" for t in state.available_tools) or "(none)"
    plan = Plan.model_validate(
        (prompt | llm).invoke(
            {
                "user_request": state.user_request,
                "conversation_context": state.conversation_context or "(none: this is the first request)",
                "available_tools": available_tools,
            }
        )
    )

    langfuse_context.update_current_observation(
        input={"user_request": state.user_request, "conversation_context": state.conversation_context},
        output=plan.model_dump(),
    )

    return state.model_copy(update={"plan": plan})


def build_graph() -> CompiledStateGraph:  # type: ignore[type-arg]  # langgraph generics are unwieldy
    g = StateGraph(State)
    g.add_node("planner", planner)
    g.add_edge(START, "planner")
    g.add_edge("planner", END)
    return g.compile()
