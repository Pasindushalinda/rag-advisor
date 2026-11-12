from enum import StrEnum

from langchain_core.prompts import ChatPromptTemplate
from langfuse import Langfuse
from langfuse.model import ChatPromptClient


class PromptName(StrEnum):
    """Every prompt stored in Langfuse. Seed scripts create them and code loads them by these names."""

    PLANNER_PLAN_STEPS = "planner_plan_steps"
    RETRIEVER_QUERY_REWRITE = "retriever_query_rewrite"
    INVESTOR_ADVISOR_SUPERVISOR = "investor_advisor_supervisor"
    INVESTOR_ADVISOR_RETRIEVAL_DECISION = "investor_advisor_retrieval_decision"
    INVESTOR_ADVISOR_ANSWER = "investor_advisor_answer"
    MARKET_RESEARCH_AGENT = "market_research_agent"
    CHUNK_JUDGE_SELF_CONTAINED = "chunk_judge_self_contained_prompt"
    CHUNK_JUDGE_EXPECTED_DETAILS = "chunk_judge_expected_details"


def get_prompt(name: PromptName) -> ChatPromptTemplate:
    """The latest version of a chat prompt as a LangChain template.

    `{{var}}` placeholders become template variables; a prompt without variables is rendered
    with `.format_messages()`. The Langfuse prompt client is in `metadata["langfuse_prompt"]`.
    """
    lf_prompt: ChatPromptClient = Langfuse().get_prompt(name.value, label="latest", type="chat")
    prompt = ChatPromptTemplate.from_messages(lf_prompt.get_langchain_prompt())
    # the langfuse_prompt metadata links generations to the prompt version in Langfuse
    prompt.metadata = {"langfuse_prompt": lf_prompt}
    return prompt
