"""Langfuse experiment: run the planner for every item of a Langfuse dataset and grade it with an LLM judge.

Scores (0-1) attached to each planner trace:
  - tool_selection: only tools from the list are used, and the right ones for the request
  - completeness:   the plan covers everything the request needs (every company/period/source)
  - step_quality:   steps are ordered, self-contained, concise and free of redundant steps
  - overall:        the judge's overall verdict

Exits non-zero if the mean overall score is below PASS_THRESHOLD.
"""

import sys
from datetime import datetime

from langchain_core.prompts import ChatPromptTemplate
from langfuse import Langfuse
from langfuse.decorators import langfuse_context
from pydantic import BaseModel, ConfigDict, Field

from graphs.planner.planner import Plan, State, Tool, build_graph
from shared.config import get_settings
from shared.llms import get_chat_model

DATASET = "planner-test-data"
PASS_THRESHOLD = 0.7


class PlannerInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    user_request: str
    available_tools: list[Tool]


class Expected(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    reference_plan: list[str]
    notes: str


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reasoning: str = Field(description="Short justification of the scores, citing concrete steps")
    tool_selection: float = Field(
        ge=0,
        le=1,
        description="1 if only listed tools are used and they are the right ones, 0 if wrong/invented",
    )
    completeness: float = Field(ge=0, le=1, description="1 if the plan covers everything the request needs")
    step_quality: float = Field(
        ge=0, le=1, description="1 if steps are ordered, self-contained, specific and not redundant"
    )
    overall: float = Field(ge=0, le=1, description="Overall quality of the plan for the request")


JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a strict evaluator of plans written by a planner for an investor advisor assistant. "
            "The plan will be executed step by step by an agent that only has the listed tools. "
            "Grade the candidate plan from 0 to 1 on each criterion. The reference plan is one good example; "
            "accept different plans that are equally valid, and penalise missing coverage, invented tools, "
            "wrong tool choice, unnecessary steps, vague steps, or guessing when information is missing.",
        ),
        (
            "user",
            "User request:\n{user_request}\n\nAvailable tools:\n{tools}\n\n"
            "What a good plan must do:\n{notes}\n\nReference plan:\n{reference}\n\n"
            "Candidate plan:\n{candidate}",
        ),
    ]
)


def numbered(steps: list[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) or "(empty plan)"


def main() -> int:
    settings = get_settings()
    judge = get_chat_model().with_structured_output(Verdict)
    planner_graph = build_graph()

    langfuse = Langfuse()
    dataset = langfuse.get_dataset(DATASET)
    run_name = f"planner-{datetime.now():%Y%m%d-%H%M%S}"
    print(f"Experiment run '{run_name}' on dataset '{DATASET}' ({len(dataset.items)} items)")

    verdicts: list[Verdict] = []
    for item in dataset.items:
        inp = PlannerInput.model_validate(item.input)
        expected = Expected.model_validate(item.expected_output)
        with item.observe(
            run_name=run_name,
            run_description="Planner quality (LLM as a judge)",
            run_metadata={"judge_model": settings.chat_model_name},
        ) as trace_id:
            state = State.model_validate(
                planner_graph.invoke(
                    State(user_request=inp.user_request, available_tools=inp.available_tools)
                )
            )
            plan = state.plan or Plan()
            langfuse_context.update_current_trace(input=inp.user_request, output=plan.model_dump())
        langfuse_context.flush()

        verdict = Verdict.model_validate(
            (JUDGE_PROMPT | judge).invoke(
                {
                    "user_request": inp.user_request,
                    "tools": "\n".join(f"- {t.name}: {t.description}" for t in inp.available_tools),
                    "notes": expected.notes,
                    "reference": numbered(expected.reference_plan),
                    "candidate": numbered(plan.steps),
                }
            )
        )
        verdicts.append(verdict)
        for name, value in verdict.model_dump(exclude={"reasoning"}).items():
            langfuse.score(trace_id=trace_id, name=name, value=value, comment=verdict.reasoning)
        print(
            f"  {inp.user_request[:55]!r} tools={verdict.tool_selection:.2f} "
            f"complete={verdict.completeness:.2f} quality={verdict.step_quality:.2f} "
            f"overall={verdict.overall:.2f}"
        )
        print(f"    plan: {plan.steps}")
        print(f"    judge: {verdict.reasoning}")

    langfuse.flush()
    n = len(verdicts)
    overall = sum(v.overall for v in verdicts) / n
    print(
        f"mean tool_selection: {sum(v.tool_selection for v in verdicts) / n:.2f}, "
        f"completeness: {sum(v.completeness for v in verdicts) / n:.2f}, "
        f"step_quality: {sum(v.step_quality for v in verdicts) / n:.2f}, overall: {overall:.2f}"
    )
    passed = overall >= PASS_THRESHOLD
    print("EXPERIMENT", "PASSED" if passed else "FAILED", f"(threshold {PASS_THRESHOLD:.2f})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
