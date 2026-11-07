"""LangGraph tools the AI calls to get Yahoo Finance data."""

from langchain_core.tools import tool

from graphs.market_research_agent.yahoo_finance import client as yahoo
from graphs.market_research_agent.yahoo_finance.models import GeneralNews, TickerData, TickerMatch


@tool(response_format="content_and_artifact", parse_docstring=True)
def find_ticker(query: str) -> tuple[str, TickerMatch]:
    """Find the Yahoo Finance ticker for a company or asset. Call this first for
    every company or asset the question is about.

    Args:
        query: The company or asset as the user wrote it, e.g. 'Apple', 'Bitcoin' or 'AAPL'.
    """
    top = yahoo.find_ticker(query.strip())
    return f"{top.symbol} ({top.name}, {top.asset_type})", top


@tool(response_format="content_and_artifact", parse_docstring=True)
def get_yahoo_finance_data(ticker: str) -> tuple[str, TickerData]:
    """Get the share price, ratios and the 5 latest news headlines for a ticker.
    Call it once per ticker.

    Args:
        ticker: The ticker returned by find_ticker, e.g. 'AAPL'.
    """
    data = yahoo.ticker_data(ticker)
    content = f"Fetched {len(data.facts)} facts and {len(data.news)} news headlines for {ticker}."
    if data.errors:
        content += " Not available (do not retry): " + "; ".join(e.part for e in data.errors)
    return content, data


@tool(response_format="content_and_artifact")
def get_general_market_news() -> tuple[str, GeneralNews]:
    """Get recent general company and market headlines when no asset is named."""
    news = yahoo.general_news()
    return f"Fetched {len(news)} recent market news headlines.", GeneralNews(items=news)


TOOLS = [find_ticker, get_yahoo_finance_data, get_general_market_news]
