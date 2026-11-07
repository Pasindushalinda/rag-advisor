from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse
from langfuse.api.core.api_error import ApiError
from pydantic import BaseModel, ConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATASET_NAME = "retrieval-test-data"


class RetrievalTestCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    ticker: str
    reference: (
        str  # what a complete answer must contain; RAGAS judges contexts against it
    )


CASES = [
    RetrievalTestCase(
        question="What are the main risk factors Apple discloses in its latest 10-K?",
        ticker="AAPL",
        reference=(
            "Apple's risk factors cover macroeconomic and industry conditions, supply chain and "
            "manufacturing dependence, intense competition, legal and regulatory matters, and "
            "financial risks such as currency and credit exposure."
        ),
    ),
    RetrievalTestCase(
        question="How many people does Microsoft employ?",
        ticker="MSFT",
        reference=(
            "Microsoft reports its total number of employees as of its fiscal year end, split "
            "between the U.S. and internationally."
        ),
    ),
    RetrievalTestCase(
        question="Which legal proceedings is Alphabet involved in?",
        ticker="GOOG",
        reference=(
            "Alphabet describes its legal proceedings, including antitrust, privacy and "
            "intellectual property matters, and refers to the legal matters note in its "
            "financial statements."
        ),
    ),
    RetrievalTestCase(
        question="How did Netflix's revenue and operating income change year over year?",
        ticker="NFLX",
        reference=(
            "Netflix compares revenue and operating income with the prior year and explains the "
            "changes, for example membership growth, pricing and content cost."
        ),
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
            input={"question": case.question},
            expected_output=case.model_dump(exclude={"question"}),
            id=f"{DATASET_NAME}-{case.ticker.lower()}",
        )

    langfuse.flush()
    print(f"Seeded {len(CASES)} items in Langfuse dataset '{DATASET_NAME}'.")


if __name__ == "__main__":
    main()
