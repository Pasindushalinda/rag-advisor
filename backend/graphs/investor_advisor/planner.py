"""Planner: turns the user's latest question into an explicit research plan.

Sits between the entry of the investor advisor graph and its research nodes. It asks the LLM
which research tools the question needs (SEC filing retrieval, market research, or none) and
converts that plan into the RetrievalDecision the graph already routes on, so the retrieve /
market_research / answer nodes stay untouched.

The LLM and prompt are injected rather than built here: investor_advisor.py owns both
(settings, Langfuse prompt loading) and imports this module, so importing them back would be
a circular import.
"""

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage
from langchain_core.prompts import ChatPromptTemplate
from langfuse.decorators import langfuse_context, observe
from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import QdrantClient

from shared.config import get_settings
from shared.models import RetrievalDecision

PLANNER_PROMPT_NAME = "investor_advisor_planner"

Tool = Literal["filings_retrieval", "market_research"]


class FilingRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    company: str
    form_type: str
    period_end: str


# Only these payload fields are fetched: scrolling full chunk text just to dedupe filings
# would be wasteful.
_FILING_FIELDS = ["ticker", "company", "form_type", "period_end"]


@observe(name="list_filings", capture_output=False)
def list_filings() -> list[FilingRef]:
    """Distinct filings in the vector store, so the plan can name real fiscal periods.

    Read from Qdrant on every turn, not cached: a cache would hide filings ingested while the
    server is running. The LLM must never guess a fiscal year; it picks from this list.
    """
    cfg = get_settings()
    client = QdrantClient(
        url=cfg.qdrant_url,
        port=None,  # without this the client appends :6333 to portless (hosted https) URLs
        timeout=cfg.qdrant_timeout,
        api_key=cfg.qdrant_api_key.get_secret_value() if cfg.qdrant_api_key else None,
    )
    filings: set[FilingRef] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            cfg.qdrant_collection,
            limit=1000,
            offset=offset,
            with_payload=_FILING_FIELDS,
            with_vectors=False,
        )
        filings.update(FilingRef.model_validate(p.payload) for p in points)
        if offset is None:
            break
    result = sorted(filings, key=lambda f: (f.ticker, f.form_type, f.period_end))
    langfuse_context.update_current_observation(
        output=[f"{f.ticker} {f.form_type} {f.period_end}" for f in result]
    )
    return result


def format_filings(filings: list[FilingRef]) -> str:
    if not filings:
        return "No filings are indexed."
    return "\n".join(
        f"- {f.company} ({f.ticker}) {f.form_type}, period ending {f.period_end}"
        for f in filings
    )


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Tool = Field(
        description=(
            "filings_retrieval: search SEC filings (10-K/10-Q) for disclosed company facts. "
            "market_research: current prices, financial ratios, or recent news."
        )
    )
    query: str = Field(
        description="Self-contained query for this tool; resolve pronouns, companies and periods"
    )
    rationale: str = Field(description="One sentence on why this step is needed")


class Plan(BaseModel):
    """LLM output: the research steps needed to answer the latest question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    standalone_question: str = Field(
        description="The latest question rewritten to be understandable without the conversation"
    )
    steps: list[PlanStep] = Field(
        description=(
            "Research steps, at most one per tool. Empty if the question is a greeting, a "
            "general concept, or fully answerable from the conversation."
        )
    )
    reason: str = Field(description="One sentence summarising the plan")


def create_plan(
    llm: BaseChatModel,
    prompt: ChatPromptTemplate,
    history: list[AnyMessage],
    question: str,
    filings: list[FilingRef],
) -> Plan:
    structured = llm.with_structured_output(Plan, method="json_schema")
    # model_validate re-checks the output; a ValidationError fails the node on purpose.
    plan = Plan.model_validate(
        (prompt | structured).invoke(
            {
                "history": history,
                "question": question,
                "filings": format_filings(filings),
            }
        )
    )
    print(plan.model_dump_json(indent=2))  # debug: show the planner's raw output
    return plan


def plan_to_decision(plan: Plan) -> RetrievalDecision:
    """Map the plan onto the typed fields the graph's conditional edges read."""
    by_tool = {step.tool: step for step in plan.steps}
    market = by_tool.get("market_research")
    filings = by_tool.get("filings_retrieval")
    return RetrievalDecision(
        needs_retrieval=filings is not None,
        reason=plan.reason,
        standalone_question=filings.query if filings else plan.standalone_question,
        needs_market_research=market is not None,
        market_question=market.query if market else plan.standalone_question,
    )
