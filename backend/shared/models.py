from pydantic import BaseModel, ConfigDict, Field


class IngestionRequest(BaseModel):
    ticker: str
    company: str
    form_type: str
    url: str


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
        description="Numbers n of the provided numbered SEC filing excerpts cited in the answer; empty if none were used. Never list market research items<<CUT_OFF_IN_PHOTO>>"
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
