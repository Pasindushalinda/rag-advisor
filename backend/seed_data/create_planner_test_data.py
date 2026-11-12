from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse
from langfuse.api.core.api_error import ApiError
from pydantic import BaseModel, ConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATASET_NAME = "planner-test-data"


class PlannerTestCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_request: str
    reference_plan: list[str]  # an ideal plan; the judge compares against it but accepts equivalent plans
    notes: str  # what a good plan must do; shown to the judge


# Mirrors the tools investor_advisor.py hands to the planner.
AVAILABLE_TOOLS = [
    {
        "name": "retrieve_chunks",
        "description": "Retrieve relevant chunks from SEC filings (10-K/10-Q) for the given query.",
    },
    {
        "name": "research_market",
        "description": "Conduct market research (news, market data, outlook) for the given query.",
    },
]

CASES = [
    PlannerTestCase(
        id="single-filing-lookup",
        user_request="How many people does Microsoft employ according to its latest 10-K?",
        reference_plan=[
            "Use retrieve_chunks to find Microsoft's number of employees in its latest 10-K.",
            "Answer with the employee count and fiscal year end.",
        ],
        notes="Simple factual question: one or two steps, filings only, no market research.",
    ),
    PlannerTestCase(
        id="two-company-comparison",
        user_request="Compare Apple's and Microsoft's main risk factors from their latest 10-Ks.",
        reference_plan=[
            "Use retrieve_chunks to find Apple's main risk factors in its latest 10-K.",
            "Use retrieve_chunks to find Microsoft's main risk factors in its latest 10-K.",
            "Compare the two companies' risk factors and summarise similarities and differences.",
        ],
        notes="One retrieval step per company, each naming the company, then a comparison step.",
    ),
    PlannerTestCase(
        id="filings-plus-market",
        user_request="How has Netflix's revenue grown and what is the market outlook for streaming?",
        reference_plan=[
            "Use retrieve_chunks to find Netflix's revenue and its year-over-year change in the latest 10-K.",
            "Use research_market to find the current market outlook for the streaming industry.",
            "Combine the filing figures and the market outlook into a final answer.",
        ],
        notes="Needs both tools: filings for revenue, market research for outlook.",
    ),
    PlannerTestCase(
        id="market-only",
        user_request="What is the latest analyst sentiment on Nvidia stock?",
        reference_plan=[
            "Use research_market to find the latest analyst sentiment on Nvidia stock.",
            "Summarise the sentiment in the final answer.",
        ],
        notes="Market question; SEC filings are irrelevant so retrieve_chunks should not be used.",
    ),
    PlannerTestCase(
        id="multi-period",
        user_request="Did Alphabet's operating income increase between fiscal 2022 and fiscal 2023?",
        reference_plan=[
            "Use retrieve_chunks to find Alphabet's operating income for fiscal 2022.",
            "Use retrieve_chunks to find Alphabet's operating income for fiscal 2023.",
            "Compare the two figures and state whether operating income increased.",
        ],
        notes="One retrieval per fiscal period, naming company and period, then a comparison.",
    ),
    PlannerTestCase(
        id="no-suitable-tool",
        user_request="Book me a flight to New York for next Monday.",
        reference_plan=["State that the request cannot be handled with the available tools."],
        notes="Out of scope: no tool fits; the plan must not invent tools or pretend to book anything.",
    ),
    PlannerTestCase(
        id="vague-request",
        user_request="Tell me about the company.",
        reference_plan=["Ask which company and what information the user wants."],
        notes="No company named; a good plan asks for missing information instead of guessing.",
    ),
]


def main() -> None:
    load_dotenv(BACKEND_ROOT / ".env")
    langfuse = Langfuse()

    try:
        langfuse.get_dataset(DATASET_NAME)
    except ApiError as error:
        if error.status_code != 404:
            raise
        langfuse.create_dataset(name=DATASET_NAME)

    for case in CASES:
        langfuse.create_dataset_item(
            dataset_name=DATASET_NAME,
            input={"user_request": case.user_request, "available_tools": AVAILABLE_TOOLS},
            expected_output=case.model_dump(include={"reference_plan", "notes"}),
            id=f"{DATASET_NAME}-{case.id}",
        )

    langfuse.flush()
    print(f"Seeded {len(CASES)} items in Langfuse dataset '{DATASET_NAME}'.")


if __name__ == "__main__":
    main()
