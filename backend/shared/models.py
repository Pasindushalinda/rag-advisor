from pydantic import BaseModel, ConfigDict, Field, field_validator


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

MAX_RETRIEVAL_TASKS = 4


class RetrievalTask(BaseModel):
    """One SEC filing search. The graph runs one retrieval worker per task, in parallel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    company: str = Field(
        description="The company this search is about, as the user named it; 'general' if it is not about one company"
    )
    question: str = Field(
        description="This company's part of the question rewritten to be understandable without the conversation (resolve pronouns, company names, fiscal periods, form types); one company per task"
    )


class Plan(BaseModel):
    """LLM output: the research the latest question needs, split into independent tasks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(description="One sentence explaining the plan")
    retrieval_tasks: list[RetrievalTask] = Field(
        description=f"SEC filing (10-K/10-Q) searches needed, one per company, at most {MAX_RETRIEVAL_TASKS}; empty if answering needs no facts from filings, or they are already in the conversation"
    )
    needs_market_research: bool = Field(
        description="True if answering requires current prices, financial ratios, or recent company/market news"
    )
    market_question: str = Field(
        description="The latest question rewritten to be understandable without the conversation, for market research"
    )

    @field_validator("retrieval_tasks")
    @classmethod
    def _within_task_limit(cls, tasks: list[RetrievalTask]) -> list[RetrievalTask]:
        # Fail the node rather than silently drop companies from a comparison: an answer that
        # quietly covers 4 of 5 companies is worse than an error. The limit also bounds the
        # parallel fan-out (each task is a rewrite LLM call plus an embedding call).
        if len(tasks) > MAX_RETRIEVAL_TASKS:
            raise ValueError(f"at most {MAX_RETRIEVAL_TASKS} retrieval tasks, got {len(tasks)}")
        return tasks

    @property
    def needs_retrieval(self) -> bool:
        return bool(self.retrieval_tasks)


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
