"""Langfuse experiment: run ingestion for every item of a Langfuse dataset and score the
resulting chunks with an LLM judge.

Each dataset item runs one ingestion (one trace, linked to the dataset run). A sample of its
chunks is judged on two criteria and the pass rates are attached to the ingestion trace as scores:
  - self_contained:          the chunk is understandable on its own
  - has_company_period_form: the chunk identifies the company, reporting period and form type

Exits non-zero if any criterion's overall pass rate is below PASS_THRESHOLD.
"""

import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from langfuse import Langfuse
from langfuse.decorators import langfuse_context
from pydantic import BaseModel, Field

from graphs.ingestion import CHUNK_OVERLAP, MAX_CHUNK_SIZE, ingest
from shared.config import get_settings
from shared.llms import get_chat_model
from shared.models import IngestionRequest
from shared.prompts import PromptName, get_prompt

DATASET = "ingestion-test-data"
JUDGE_CONCURRENCY = 2  # the Azure chat deployment's quota rate-limits higher concurrency
SAMPLE_SIZE = 10  # 0 = judge every chunk
PASS_THRESHOLD = 0.8
MAX_JUDGE_CHARS = 8000

# Criterion -> Langfuse chat prompt; variables: {{chunk}}, plus {{expected}} for has_company_period_form
PROMPT_NAMES = {
    "self_contained": PromptName.CHUNK_JUDGE_SELF_CONTAINED,
    "has_company_period_form": PromptName.CHUNK_JUDGE_EXPECTED_DETAILS,
}


class Verdict(BaseModel):
    reason: str = Field(description="One or two sentences justifying the verdict")
    passed: bool = Field(description="True if the chunk meets the criterion")


settings = get_settings()
JUDGE_MODEL = settings.chat_model_name
langfuse = Langfuse()
judge = get_chat_model(max_tokens=8192).with_structured_output(
    Verdict, include_raw=True
)

templates = {c: get_prompt(n) for c, n in PROMPT_NAMES.items()}


def judge_chunk(
    trace_id: str,
    criterion: str,
    chunk: dict[str, str | int],
    expected: dict[str, str | None],
) -> dict[str, str | int | bool]:
    template = templates[criterion]
    messages = template.format_messages(
        chunk=str(chunk["data"])[:MAX_JUDGE_CHARS], expected=json.dumps(expected)
    )
    gen = langfuse.generation(
        trace_id=trace_id,
        name=f"judge-{criterion}",
        model=JUDGE_MODEL,
        prompt=(template.metadata or {})["langfuse_prompt"],
        input=[{"role": m.type, "content": m.content} for m in messages],
        metadata={"chunk_num": chunk["chunk_num"], "file": chunk["file"]},
    )
    verdict, last_error = None, None
    for _ in range(3):  # retry on unparsable output
        resp = judge.invoke(messages)
        if resp["parsed"] is not None:
            verdict = {"pass": resp["parsed"].passed, "reason": resp["parsed"].reason}
            break
        last_error = resp["parsing_error"]
    usage = resp["raw"].usage_metadata
    gen.end(
        output=verdict or resp["raw"].content,
        usage={"input": usage["input_tokens"], "output": usage["output_tokens"]}
        if usage
        else None,
        level="DEFAULT" if verdict else "ERROR",
        status_message=None if verdict else str(last_error),
    )
    return {
        "chunk_num": chunk["chunk_num"],
        "criterion": criterion,
        **(verdict or {"pass": False, "reason": f"judge error: {last_error}"}),
    }


def evaluate(
    item_input: dict[str, str],
    expected: dict[str, str],
    chunks: list[dict[str, str | int]],
    ingestion_trace_id: str,
    run_name: str,
) -> tuple[dict[str, float], list[dict[str, str | int | bool]]]:
    sample = chunks
    if SAMPLE_SIZE and len(chunks) > SAMPLE_SIZE:
        sample = random.Random(expected.get("file") or item_input["url"]).sample(
            chunks, SAMPLE_SIZE
        )
    expected_meta = {
        k: expected.get(k) for k in ("company", "ticker", "form_type", "period_end")
    }

    eval_trace = langfuse.trace(
        name="chunk-evaluation",
        input={"filing": item_input, "chunk_nums": [c["chunk_num"] for c in sample]},
        metadata={
            "ingestion_trace_id": ingestion_trace_id,
            "run_name": run_name,
            "judge_model": JUDGE_MODEL,
        },
        tags=["evaluation", item_input.get("ticker", "")],
    )
    jobs = [(c, crit) for c in sample for crit in PROMPT_NAMES]
    with ThreadPoolExecutor(max_workers=JUDGE_CONCURRENCY) as pool:
        results = list(
            pool.map(
                lambda j: judge_chunk(eval_trace.id, j[1], j[0], expected_meta), jobs
            )
        )

    scores = {}
    for crit in PROMPT_NAMES:
        rs = [r for r in results if r["criterion"] == crit]
        score = sum(r["pass"] for r in rs) / len(rs)
        failed = [f"#{r['chunk_num']}: {r['reason']}" for r in rs if not r["pass"]]
        comment = f"{sum(r['pass'] for r in rs)}/{len(rs)} sampled chunks passed"
        if failed:
            comment += ". Failures: " + " | ".join(failed)
        langfuse.score(
            trace_id=ingestion_trace_id, name=crit, value=score, comment=comment[:4000]
        )
        scores[crit] = score
    eval_trace.update(output={"scores": scores, "results": results})
    return scores, results


def main() -> int:
    dataset = langfuse.get_dataset(DATASET)
    run_name = f"chunking-{datetime.now():%Y%m%d-%H%M%S}"
    run_metadata = {
        "judge_model": JUDGE_MODEL,
        "sample_size": SAMPLE_SIZE,
        "max_chunk_size": MAX_CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
    }
    print(
        f"Experiment run '{run_name}' on dataset '{DATASET}' ({len(dataset.items)} items)"
    )

    all_results: list[dict[str, str | int | bool]] = []
    for item in dataset.items:
        with item.observe(
            run_name=run_name,
            run_description="Chunking quality (LLM judge)",
            run_metadata=run_metadata,
        ) as trace_id:
            chunks = ingest(
                IngestionRequest.model_validate(item.input), store_chunks=False
            )
        langfuse_context.flush()
        scores, results = evaluate(
            item.input, item.expected_output or {}, chunks, trace_id, run_name
        )
        all_results += results
        print(
            f"  {item.input.get('ticker')}: {len(chunks)} chunks, "
            + ", ".join(f"{k}={v:.2f}" for k, v in scores.items())
        )
        for r in results:
            if not r["pass"]:
                print(f"    FAIL {r['criterion']} #{r['chunk_num']}: {r['reason']}")

    langfuse.flush()
    passed = True
    for crit in PROMPT_NAMES:
        rs = [r for r in all_results if r["criterion"] == crit]
        rate = sum(r["pass"] for r in rs) / len(rs)
        ok = rate >= PASS_THRESHOLD
        passed &= ok
        print(
            f"{crit}: {rate:.2%} ({'PASS' if ok else 'FAIL'}), threshold {PASS_THRESHOLD:.0%}"
        )
    print("EXPERIMENT", "PASSED" if passed else "FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())