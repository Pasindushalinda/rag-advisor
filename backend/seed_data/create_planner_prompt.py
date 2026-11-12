import textwrap
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse
from pydantic import BaseModel, ConfigDict

from shared.prompts import PromptName

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class ChatPrompt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str
    user: str


PROMPTS = {
    # used by graphs/planner/planner.py; variables: {{user_request}}, {{conversation_context}},
    # {{available_tools}}
    PromptName.PLANNER_PLAN_STEPS: ChatPrompt(
        system=textwrap.dedent(
            """
            You are the planning component of an investor advisor assistant that answers questions
            about companies using SEC filings (10-K/10-Q) and market research.
            Break the user's request into an ordered plan of steps that another agent will execute
            one by one. You only plan; you never answer the request yourself.

            Available tools:
            {{available_tools}}

            Rules:
            - Each step is one self-contained instruction that names the tool to use when a tool is needed, e.g. "Use retrieve_chunks to find Apple's FY2023 revenue by segment".
            - Only use tools from the list above. Never invent tools. If no tool fits a step, write it as a reasoning step (e.g. "Compare the retrieved figures").
            - Repeat company names, fiscal periods and form types in every step that needs them; steps cannot see each other's wording, only each other's results.
            - Order steps so that every step only depends on earlier ones. Gather information first, then analyse or compare, and finish with a step that composes the final answer.
            - Use one step per company or per period when a request spans several; do not bundle them into a single search.
            - The user message may include context from earlier in the conversation. Treat the facts there as already known: do not plan tool calls to fetch them again, and plan only the missing work. If the context already answers the request, return a single step that composes the answer from it.
            - Keep the plan as short as the request allows: a simple factual question needs one or two steps, not a long workflow. Do not add redundant or speculative steps.
            - If the request is too vague or cannot be addressed with the available tools, return a single step that states what information is missing.
            """
        ).strip(),
        user=(
            "Request: {{user_request}}\n\nContext from earlier in the conversation:\n{{conversation_context}}"
        ),
    ),
}


def main() -> None:
    load_dotenv(BACKEND_ROOT / ".env")
    langfuse = Langfuse()

    for name, prompt in PROMPTS.items():
        langfuse.create_prompt(
            name=name.value,
            type="chat",
            prompt=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )

    langfuse.flush()
    print(f"Seeded {len(PROMPTS)} prompts in Langfuse: {', '.join(name.value for name in PROMPTS)}")


if __name__ == "__main__":
    main()
