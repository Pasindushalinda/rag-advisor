import textwrap
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse
from pydantic import BaseModel, ConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class ChatPrompt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str
    user: str


PROMPTS = {
    # used by graphs/retriever/retriever.py; variable: {{question}}
    "retriever_query_rewrite": ChatPrompt(
        system=textwrap.dedent(
            """
            You prepare questions for semantic search over a vector index of SEC filings (10-K/10-Q) from several companies.
            Rewrite the question as a self-contained search query. Keep the company name and any fiscal period or form type the question mentions, since the <<CUT_OFF_IN_PHOTO>>
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
            name=name,
            type="chat",
            prompt=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )

    langfuse.flush()
    print(f"Seeded {len(PROMPTS)} prompts in Langfuse: {', '.join(PROMPTS)}")


if __name__ == "__main__":
    main()
