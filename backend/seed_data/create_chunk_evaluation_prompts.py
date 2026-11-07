import textwrap
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse

BACKEND_ROOT = Path(__file__).resolve().parents[1]


chunk_judge_self_contained_prompt = {
    "system": textwrap.dedent(
        """
        You are evaluating a chunk produced for a retrieval-augmented generation (RAG) index over SEC filings.

        Criterion: Is the chunk SELF-CONTAINED?
        A chunk is self-contained if a reader who sees ONLY this chunk (retrieved in isolation) can understand what it is about and what its content means. The first line is a c<<CUT_OFF_IN_PHOTO>>
        Pass if:
        - the topic/subject is clear from the header and text, and
        - statements, numbers and table rows can be interpreted (it is clear what they refer to), and
        - it does not depend on unresolved references to content outside the chunk (e.g. "as discussed above", "the following table" with no table, pronouns with no antecedent)<<CUT_OFF_IN_PHOTO>>
        Minor cross-references to other Items/Notes are acceptable. Starting or ending mid-paragraph is acceptable as long as the meaning stays clear.
        Fail if the chunk is a fragment whose meaning cannot be determined without neighbouring chunks, a table whose columns/rows cannot be interpreted, or a heading/boilerplat<<CUT_OFF_IN_PHOTO>>
        """
    ),
    "user": textwrap.dedent(
        """
        <chunk>
        {{chunk}}
        </chunk>
        """
    )
}

chunk_judge_expected_details = {
    "system": textwrap.dedent(
        """
        You are evaluating a chunk produced for a retrieval-augmented generation (RAG) index over SEC filings.

        Criterion: Does the chunk text itself identify ALL of the following?
        1. the company (name or ticker)
        2. the reporting period (fiscal year/quarter or period end date)
        3. the form type (e.g. 10-K, 10-Q)
        The information may appear anywhere in the chunk, including its first-line context header. It must be consistent with the expected values below; ignore case and punctuat<<CUT_OFF_IN_PHOTO>>
        Expected values: {{expected}}
        Pass only if all three are present and correct.
        """
    ),
    "user": textwrap.dedent(
        """
        <chunk>
        {{chunk}}
        </chunk>
        """
    )
}


PROMPTS = {
    "chunk_judge_self_contained_prompt": chunk_judge_self_contained_prompt,
    "chunk_judge_expected_details": chunk_judge_expected_details,
}


def main() -> None:
    load_dotenv(BACKEND_ROOT / ".env")
    langfuse = Langfuse()

    for name, prompt in PROMPTS.items():
        langfuse.create_prompt(
            name=name,
            type="chat",
            prompt=[
                {"role": role, "content": prompt[role]}
                for role in ("system", "user")
            ],
        )

    langfuse.flush()
    print(f"Seeded {len(PROMPTS)} prompts in Langfuse: {', '.join(PROMPTS)}")


if __name__ == "__main__":
    main()


import textwrap
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse


BACKEND_ROOT = Path(__file__).resolve().parents[1]


chunk_judge_self_contained_prompt = {
    "system": textwrap.dedent(
        """
        You are evaluating a chunk produced for a retrieval-augmented generation (RAG) index over SEC filings.

        Criterion: Is the chunk SELF-CONTAINED?
        A chunk is self-contained if a reader who sees ONLY this chunk (retrieved in isolation) can understand what it is about and what its content means. The first line is a c<<CUT_OFF_IN_PHOTO>>
        Pass if:
        - the topic/subject is clear from the header and text, and
        - statements, numbers and table rows can be interpreted (it is clear what they refer to), and
        - it does not depend on unresolved references to content outside the chunk (e.g. "as discussed above", "the following table" with no table, pronouns with no antecedent)<<CUT_OFF_IN_PHOTO>>
        Minor cross-references to other Items/Notes are acceptable. Starting or ending mid-paragraph is acceptable as long as the meaning stays clear.
        Fail if the chunk is a fragment whose meaning cannot be determined without neighbouring chunks, a table whose columns/rows cannot be interpreted, or a heading/boilerplat<<CUT_OFF_IN_PHOTO>>
        """
    ),
    "user": textwrap.dedent(
        """
        <chunk>
        {{chunk}}
        </chunk>
        """
    )
}

chunk_judge_expected_details = {
    "system": textwrap.dedent(
        """
        You are evaluating a chunk produced for a retrieval-augmented generation (RAG) index over SEC filings.

        Criterion: Does the chunk text itself identify ALL of the following?
        1. the company (name or ticker)
        2. the reporting period (fiscal year/quarter or period end date)
        3. the form type (e.g. 10-K, 10-Q)
        The information may appear anywhere in the chunk, including its first-line context header. It must be consistent with the expected values below; ignore case and punctuat<<CUT_OFF_IN_PHOTO>>
        Expected values: {{expected}}
        Pass only if all three are present and correct.
        """
    ),
    "user": textwrap.dedent(
        """
        <chunk>
        {{chunk}}
        </chunk>
        """
    )
}


PROMPTS = {
    "chunk_judge_self_contained_prompt": chunk_judge_self_contained_prompt,
    "chunk_judge_expected_details": chunk_judge_expected_details,
}


def main() -> None:
    load_dotenv(BACKEND_ROOT / ".env")
    langfuse = Langfuse()

    for name, prompt in PROMPTS.items():
        langfuse.create_prompt(
            name=name,
            type="chat",
            prompt=[
                {"role": role, "content": prompt[role]}
                for role in ("system", "user")
            ],
        )

    langfuse.flush()
    print(f"Seeded {len(PROMPTS)} prompts in Langfuse: {', '.join(PROMPTS)}")


if __name__ == "__main__":
    main()
