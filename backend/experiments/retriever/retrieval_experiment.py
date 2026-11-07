"""Langfuse experiment: run the retriever for every item of a Langfuse dataset and evaluate it with RAGAS.

Scores attached to each retrieval trace:
  - context_precision: RAGAS LLMContextPrecisionWithReference (are relevant chunks ranked first?)
  - context_recall:    RAGAS LLMContextRecall (is the reference answer covered by the chunks?)

Exits non-zero if the mean context_precision or context_recall is below PASS_THRESHOLD.
"""

import asyncio
import math
import sys
from datetime import datetime

from shared.compat import install_shims

install_shims()  # must run before langfuse's LangChain handler and ragas are imported

from langchain_openai import ChatOpenAI
from langfuse import Langfuse
from langfuse.callback import CallbackHandler
from langfuse.decorators import langfuse_context
from pydantic import BaseModel, ConfigDict
from ragas.dataset_schema import SingleTurnSample
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall

from graphs.retriever import DEFAULT_TOP_K, retrieve
from shared.config import get_settings
from shared.models import RetrievalResult

DATASET = "retrieval-test-data"
TOP_K = DEFAULT_TOP_K
PASS_THRESHOLD = 0.6


class Question(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str


class Expected(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    reference: str


class Scores(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context_precision: float
    context_recall: float


settings = get_settings()
judge = LangchainLLMWrapper(
    ChatOpenAI(
        model=settings.chat_model_name,
        base_url=settings.chat_model_endpoint,
        api_key=settings.chat_model_api_key,  # type: ignore[arg-type]  # str accepted at runtime
        max_retries=6,
        timeout=120,
    )
)
precision_metric = LLMContextPrecisionWithReference(llm=judge)
recall_metric = LLMContextRecall(llm=judge)


async def ragas_scores(
    result: RetrievalResult, reference: str, handler: CallbackHandler
) -> tuple[float, float]:
    sample = SingleTurnSample(
        user_input=result.question,
        retrieved_contexts=[c.data for c in result.chunks],
        reference=reference,
    )
    # callbacks nest the judge LLM calls under the retrieval trace in Langfuse
    precision, recall = await asyncio.gather(
        precision_metric.single_turn_ascore(sample, callbacks=[handler]),
        recall_metric.single_turn_ascore(sample, callbacks=[handler]),
    )
    return float(precision), float(recall)


def mean(values: list[float]) -> float:
    valid = [
        v for v in values if not math.isnan(v)
    ]  # NaN = RAGAS judge output was unparsable
    return sum(valid) / len(valid) if valid else float("nan")


def main() -> int:
    langfuse = Langfuse()
    dataset = langfuse.get_dataset(DATASET)
    run_name = f"retrieval-{datetime.now():%Y%m%d-%H%M%S}"
    print(
        f"Experiment run '{run_name}' on dataset '{DATASET}' ({len(dataset.items)} items)"
    )

    all_scores: list[Scores] = []
    # One loop for all items: the judge's async HTTP client is bound to the loop it first runs on.
    loop = asyncio.new_event_loop()
    for item in dataset.items:
        question = Question.model_validate(item.input)
        expected = Expected.model_validate(item.expected_output)
        with item.observe(
            run_name=run_name,
            run_description="Retrieval quality (RAGAS)",
            run_metadata={"top_k": TOP_K, "judge_model": settings.chat_model_name},
        ) as trace_id:
            result = retrieve(question.question, top_k=TOP_K)
        langfuse_context.flush()

        handler = langfuse.trace(id=trace_id).get_langchain_handler()
        precision, recall = loop.run_until_complete(
            ragas_scores(result, expected.reference, handler)
        )
        handler.flush()

        scores = Scores(
            context_precision=precision,
            context_recall=recall,
        )
        all_scores.append(scores)
        for name, value in scores.model_dump().items():
            if not math.isnan(value):
                langfuse.score(trace_id=trace_id, name=name, value=value)
        print(
            f"  {item.input['question'][:60]!r} precision={scores.context_precision:.2f} "
            f"recall={scores.context_recall:.2f}"
        )

    langfuse.flush()
    precision = mean([s.context_precision for s in all_scores])
    recall = mean([s.context_recall for s in all_scores])
    print(f"context_precision: {precision:.2f}, context_recall: {recall:.2f}")
    passed = precision >= PASS_THRESHOLD and recall >= PASS_THRESHOLD
    print(
        "EXPERIMENT",
        "PASSED" if passed else "FAILED",
        f"(threshold {PASS_THRESHOLD:.2f})",
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())