from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

SEC_FILING_URL_FORMAT = "https://www.sec.gov/Archives/edgar/data/"
SEC_FILING_URL_PATH_PREFIX = "/Archives/edgar/data/"


class IngestionRequest(BaseModel):
    ticker: str
    company: str
    form_type: str
    url: str

    @field_validator("url")
    @classmethod
    def validate_sec_filing_url(cls, value: str) -> str:
        parsed_url = urlsplit(value)
        if (
            parsed_url.scheme.lower() != "https"
            or parsed_url.netloc.lower() != "www.sec.gov"
            or not parsed_url.path.startswith(SEC_FILING_URL_PATH_PREFIX)
            or len(parsed_url.path) <= len(SEC_FILING_URL_PATH_PREFIX)
        ):
            raise ValueError(f"Invalid SEC filing URL. It must start with: {SEC_FILING_URL_FORMAT}")
        return value


class IngestionJob(BaseModel):
    """Status of a background ingestion run, as returned by the /ingest endpoints."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    ticker: str
    company: str
    status: Literal["pending", "done", "failed"]
    progress: int = Field(default=0, ge=0, le=100)
    message: str | None = None


class IndexedFilingPayload(BaseModel):
    """The Qdrant point payload fields needed to identify an indexed filing."""

    model_config = ConfigDict(extra="ignore")

    ticker: str
    company: str
    form_type: str
    period_end: str
    file: str


class IndexedAnnualFiling(BaseModel):
    """One 10-K already indexed in Qdrant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    company: str
    year: int
    period_end: str
    file: str


class QueryRewrite(BaseModel):
    """LLM output: the question rewritten for semantic search."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    search_query: str = Field(
        description="Self-contained query optimised for semantic search over SEC filing passages"
    )


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float
    ticker: str
    company: str
    form_type: str
    period_end: str
    file: str
    chunk_num: int
    data: str


class RetrievalResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    rewrite: QueryRewrite
    chunks: list[RetrievedChunk]


class RetrievalDecision(BaseModel):
    """LLM output: whether answering the latest question needs SEC filing passages."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    needs_retrieval: bool = Field(
        description="True if answering requires facts from company SEC filings (10-K/10-Q) not already in the conversation"
    )
    reason: str = Field(description="One sentence explaining the decision")
    standalone_question: str = Field(
        description="The latest question rewritten to be understandable without the conversation (resolve pronouns, company names, periods)"
    )
    needs_market_research: bool = Field(
        description="True if answering requires current prices, financial ratios, or recent company/market news"
    )
    market_question: str = Field(
        description="The latest question rewritten to be understandable without the conversation, for market research"
    )


class AdvisorLLMAnswer(BaseModel):
    """LLM output: the answer plus the numbers of the sources it relied on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str = Field(
        description="Answer to the investor's question; cite sources inline as [n]"
    )
    sources: list[int] = Field(
        description=(
            "Numbers n of the provided numbered SEC filing excerpts cited in the answer; "
            "empty if none were used. Never list market research items here"
        )
    )


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: int
    label: str
    file: str
    chunk_num: int


class AdvisorAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str
    citations: list[Citation]
