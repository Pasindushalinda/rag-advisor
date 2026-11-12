import textwrap
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse
from pydantic import BaseModel, ConfigDict

from shared.prompts import PromptName

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class ChatPrompt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str
    user: str


PROMPTS = {
    # used by graphs/retriever/retriever.py; variable: {{question}}
    PromptName.RETRIEVER_QUERY_REWRITE: ChatPrompt(
        system=textwrap.dedent(
            """
            You prepare questions for semantic search over a vector index of SEC filings (10-K/10-Q) from several companies.
            Rewrite the question as a self-contained search query. Keep the company name and any fiscal period or form type the question mentions, since the search is not filtered by metadata.
            Expand vague wording into the terms a filing would use.
            """
        ).strip(),
        user="{{question}}",
    ),
}


def main() -> None:
    load_dotenv(BACKEND_ROOT / ".env")
    langfuse = Langfuse()

    for name, prompt in PROMPTS.items():
        langfuse.create_prompt(
            name=name.value,
            type="chat",
            prompt=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )

    langfuse.flush()
    print(f"Seeded {len(PROMPTS)} prompts in Langfuse: {', '.join(name.value for name in PROMPTS)}")


if __name__ == "__main__":
    main()
