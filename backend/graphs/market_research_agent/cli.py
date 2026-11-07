"""Command-line runner for the Market Data agent (`uv run market-agent`)."""

import logging
import warnings

from pandas.errors import Pandas4Warning

from graphs.market_research_agent.graph import build_graph
from graphs.market_research_agent.output import format_fetch_plan, format_market_facts, market_results
from graphs.market_research_agent.state import AgentState
from shared.config import get_settings


def main() -> None:
    warnings.filterwarnings("ignore", category=Pandas4Warning, module=r"yfinance\.")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    graph = build_graph(get_settings())
    print("Market Data agent ready. Type a question, or 'exit' to quit.\n")
    while True:
        question = input("Your question: ").strip()
        if question.lower() in {"exit", "quit", "q"}:
            break
        if not question:
            continue
        print("Planning...\n", flush=True)
        try:
            state = AgentState(question=question)
            plan_shown = False
            for snapshot in graph.stream(state, stream_mode="values"):
                results = market_results(AgentState.model_validate(snapshot))
                if not plan_shown and (results.companies or "get_general_market_news" in results.tools_used):
                    print(format_fetch_plan(results) + "\n", flush=True)
                    plan_shown = True
            print("TOOLS USED:", results.tools_used)
            print(format_market_facts(results))
        except Exception as e:
            print(f"Something went wrong: {e}")
        print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
