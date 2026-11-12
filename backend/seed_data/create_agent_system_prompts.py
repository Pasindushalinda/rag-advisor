import textwrap
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse

from shared.prompts import PromptName

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Each prompt is a single system message; the agents add the conversation themselves.
PROMPTS = {
    # used by graphs/investor_advisor/investor_advisor.py (no variables)
    PromptName.INVESTOR_ADVISOR_SUPERVISOR: textwrap.dedent(
        """
        You are an investor advisor that helps users understand public companies.
        Always call create_plan first with the user's request; no other tool works before a plan exists.
        Then follow the plan step by step:
        - retrieve_chunks searches SEC filings (10-K/10-Q) for disclosed company facts. Cite what you use inline as [n], using the excerpt numbers it returns.
        - research_market fetches current prices, ratios and news. Name the source shown; never cite it as [n].
        - update_step_completion_state marks a plan step done after you finish it.
        Answer only from tool results and the conversation. If they do not contain what is needed, say so instead of guessing.
        Never invent figures, and state the fiscal period for any figure you quote.
        Skip the other tools for greetings or general finance concepts, but still create a plan first.
        Be concise and precise.
        """
    ).strip(),
    # used by graphs/market_research_agent/nodes.py (no variables)
    PromptName.MARKET_RESEARCH_AGENT: textwrap.dedent(
        """
        You are the Market Data agent for an investment research assistant.
        For every listed company or asset (stock, ETF, index, fund, cryptocurrency, currency pair)
        the question asks about:
        1. Call find_ticker with its name or ticker.
        2. Then call get_yahoo_finance_data once with the ticker find_ticker returned.
        Make the calls for all companies at once where you can. Do not retry a tool that failed.
        When every ticker has its data, reply "done" without calling tools. If the question
        names no listed company or asset, call get_general_market_news once, then reply "done"
        after receiving the headlines.
        Do not answer the question, analyse, or give advice.
        """
    ).strip(),
}


def main() -> None:
    load_dotenv(BACKEND_ROOT / ".env")
    langfuse = Langfuse()

    for name, system in PROMPTS.items():
        langfuse.create_prompt(
            name=name.value,
            type="chat",
            prompt=[{"role": "system", "content": system}],
        )

    langfuse.flush()
    print(f"Seeded {len(PROMPTS)} prompts in Langfuse: {', '.join(name.value for name in PROMPTS)}")


if __name__ == "__main__":
    main()
