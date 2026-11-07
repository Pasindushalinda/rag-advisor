from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse
from langfuse.api.core.api_error import ApiError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATASET_NAME = "ingestion-test-data"


@dataclass(frozen=True)
class IngestionTestData:
    input_data: dict[str,str]
    expected_result: dict[str,str]

data: list[IngestionTestData] = [
    IngestionTestData(
        input_data={"ticker": "AAPL", "company": "Apple Inc", "form_type": "10-K", "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"},
        expected_result={"ticker": "AAPL", "company": "apple inc", "form_type": "10-K", "period_end": "2025-09-27", "file": "aapl-20250927.htm"}
    ),
    IngestionTestData(
        input_data={"ticker": "MSFT", "company": "Microsoft Corporation", "form_type": "10-K", "url": "https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm"},
        expected_result={"ticker": "MSFT", "company": "microsoft corporation", "form_type": "10-K", "period_end": "2026-06-30", "file": "msft-20260630.htm"}
    ),
    IngestionTestData(
        input_data={"ticker": "NFLX", "company": "Netflix Inc", "form_type": "10-K", "url": "https://www.sec.gov/Archives/edgar/data/1065280/000106528026000034/nflx-20251231.htm"},
        expected_result={"ticker": "NFLX", "company": "netflix inc", "form_type": "10-K", "period_end": "2025-12-31", "file": "nflx-20251231.htm"}
    ),
    IngestionTestData(
        input_data={"ticker": "GOOG", "company": "Alphabet Inc", "form_type": "10-K", "url": "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm"},
        expected_result={"ticker": "GOOG", "company": "alphabet inc", "form_type": "10-K", "period_end": "2025-12-31", "file": "goog-20251231.htm"}
    )
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

    for test_case in data:
        langfuse.create_dataset_item(
            dataset_name=DATASET_NAME,
            input=test_case.input_data,
            expected_output=test_case.expected_result,
            id=f"{DATASET_NAME}-{test_case.input_data['ticker'].lower()}",
        )

    langfuse.flush()
    print(f"Seeded {len(data)} items in Langfuse dataset '{DATASET_NAME}'.")


if __name__ == "__main__":
    main()
