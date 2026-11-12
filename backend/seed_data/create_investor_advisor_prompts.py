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


# Used by graphs/investor_advisor/investor_advisor.py, which inserts the conversation
# history between the system and user messages, so each prompt must be exactly [system, user].
PROMPTS = {
    # variable: {{question}}
    PromptName.INVESTOR_ADVISOR_RETRIEVAL_DECISION: ChatPrompt(
        system=textwrap.dedent(
            """
            You route questions for an investor advisor that can search a vector index of SEC filings (10-K/10-Q) from several companies.
            Decide whether answering the user's latest question requires passages from those filings.
            Retrieve when the question asks about a specific company's financials, results, guidance, risks, segments, strategy, management discussion or other facts disclosed in its filings, unless the conversation already contains those facts.
            Do not retrieve for greetings, general investing or finance concepts, or follow-ups fully answerable from the conversation so far.
            Decide separately whether current prices, financial ratios, or recent company/market news
            are needed. Request market research for those questions, including follow-ups that refer
            to a company in the conversation.
            Rewrite both standalone_question and market_question so each is understandable without the
            conversation: resolve pronouns and references to companies, fiscal periods and form types.
            """
        ).strip(),
        user="{{question}}",
    ),
    # variables: {{question}}, {{sources}}
    PromptName.INVESTOR_ADVISOR_ANSWER: ChatPrompt(
        system=textwrap.dedent(
            """
            You are an investor advisor helping users understand public companies from their SEC filings.
            Answer the user's latest question using the conversation and the research sources provided.
            Base disclosed/historical company facts on the numbered filing excerpts and cite them inline
            as [n]; list each cited n in sources.
            Use market research for current prices, ratios, and news, and name the source (publisher or
            URL) shown. Never cite market research as [n].
            If the sources do not contain the information needed, say so instead of guessing. Do not
            invent figures.
            Be concise and precise, and state the fiscal period for any figure you quote.
            """
        ).strip(),
        user=textwrap.dedent(
            """
            Question: {{question}}

            Filing excerpts:
            {{sources}}
            """
        ).strip(),
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
