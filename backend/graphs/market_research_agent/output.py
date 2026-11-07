"""Collects the agent's results from the final state and formats them as text."""

from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, ConfigDict

from graphs.market_research_agent.state import AgentState
from graphs.market_research_agent.yahoo_finance.models import (
    DataPart,
    GeneralNews,
    MarketFact,
    NewsItem,
    TickerData,
    TickerMatch,
)

type StepName = Literal["agent", "find_ticker", "yahoo_finance", "get_general_market_news"] | DataPart


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ResolvedCompany(_Frozen):
    query: str
    ticker: str
    name: str | None
    asset_type: str | None


class MarketError(_Frozen):
    step: StepName
    message: str


class MarketResults(_Frozen):
    companies: list[ResolvedCompany]
    facts: list[MarketFact]
    news: list[NewsItem]
    errors: list[MarketError]
    tools_used: list[StepName]


def market_results(state: AgentState) -> MarketResults:
    call_arg = {
        c["id"]: str(next(iter(c["args"].values()), ""))
        for m in state.messages
        if isinstance(m, AIMessage)
        for c in m.tool_calls
    }
    companies: dict[str, ResolvedCompany] = {}
    data: dict[str, TickerData] = {}
    general_news: list[NewsItem] = []
    errors: list[MarketError] = []
    used: set[StepName] = set()
    for m in state.messages:
        if not isinstance(m, ToolMessage):
            continue
        label = call_arg.get(m.tool_call_id, "")
        if m.status == "error":
            step: StepName = (
                "find_ticker"
                if m.name == "find_ticker"
                else "get_general_market_news"
                if m.name == "get_general_market_news"
                else "yahoo_finance"
            )
            used.add(step)
            errors.append(MarketError(step=step, message=f"{label}: {m.content}"))
        elif isinstance(m.artifact, TickerMatch):
            used.add("find_ticker")
            match = m.artifact
            company = ResolvedCompany(
                query=label, ticker=match.symbol, name=match.name, asset_type=match.asset_type
            )
            companies.setdefault(match.symbol, company)
        elif isinstance(m.artifact, TickerData):
            used.update(("price", "ratios", "news"))
            data.setdefault(m.artifact.ticker, m.artifact)
        elif isinstance(m.artifact, GeneralNews):
            used.add("get_general_market_news")
            general_news.extend(m.artifact.items)
    for d in data.values():
        errors += [MarketError(step=e.part, message=f"{d.ticker}: {e.message}") for e in d.errors]
    finished = any(isinstance(m, AIMessage) for m in state.messages)
    if finished and not call_arg:
        errors.append(MarketError(step="agent", message="The question names no listed company or asset."))
    return MarketResults(
        companies=list(companies.values()),
        facts=[f for d in data.values() for f in d.facts],
        news=general_news + [n for d in data.values() for n in d.news],
        errors=list(dict.fromkeys(errors)),
        tools_used=sorted(used),
    )


def format_fetch_plan(results: MarketResults) -> str:
    if results.companies:
        lines = ["Fetching share price, ratios and the latest 5 news from Yahoo Finance for:"]
    else:
        lines = ["Fetching the latest general company and market news:"]
    lines += [f"  {c.ticker}" + (f" ({c.name})" if c.name else "") for c in results.companies]
    lines += [f"  Not found: {e.message}" for e in results.errors if e.step == "find_ticker"]
    return "\n".join(lines)


def format_market_facts(results: MarketResults) -> str:
    lines = [
        f"[{f.ticker}] {f.metric} = {f.value} {f.unit} ({f.period}; source: {f.source})"
        for f in results.facts
    ]
    lines += [f"[{n.ticker}] NEWS: {n.title} ({n.publisher}, {n.published}, {n.url})" for n in results.news]
    # lines += [f"DATA ERROR ({e.step}): {e.message}" for e in results.errors]
    return "\n".join(lines) or "No market data available."
