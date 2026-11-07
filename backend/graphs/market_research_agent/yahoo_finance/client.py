"""Fetches tickers, share price, ratios and news from Yahoo Finance."""

import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from numbers import Real
from types import MappingProxyType

import yfinance as yf

from graphs.market_research_agent.cache import ttl_cache
from graphs.market_research_agent.yahoo_finance.models import (
    DataError,
    DataPart,
    FactValue,
    MarketFact,
    NewsItem,
    TickerData,
    TickerMatch,
)

_PRICE_TTL = 300
_NEWS_TTL = 900
_SLOW_TTL = 86400

_QUOTE_TYPES = {"EQUITY", "ETF", "INDEX", "MUTUALFUND", "CRYPTOCURRENCY", "CURRENCY"}

_CUR = "{currency}"

type FieldSpec = tuple[str, str | tuple[str, ...], str]

PRICE_FIELDS: list[FieldSpec] = [
    ("current_price", ("currentPrice", "regularMarketPrice"), _CUR),
    ("previous_close", ("previousClose", "regularMarketPreviousClose"), _CUR),
    ("open", ("open", "regularMarketOpen"), _CUR),
    ("day_high", ("dayHigh", "regularMarketDayHigh"), _CUR),
    ("day_low", ("dayLow", "regularMarketDayLow"), _CUR),
    ("day_change", "regularMarketChange", _CUR),
    ("day_change_percent", "regularMarketChangePercent", "%"),
    ("volume", ("volume", "regularMarketVolume"), "shares"),
    ("average_volume_3_month", "averageVolume", "shares"),
    ("52_week_high", "fiftyTwoWeekHigh", _CUR),
    ("52_week_low", "fiftyTwoWeekLow", _CUR),
    ("52_week_change", "52WeekChange", "fraction"),
    ("50_day_average", "fiftyDayAverage", _CUR),
    ("200_day_average", "twoHundredDayAverage", _CUR),
    ("market_cap", "marketCap", _CUR),
    ("market_state", "marketState", ""),
    ("quote_delay", "exchangeDataDelayedBy", "minutes"),
]

RATIO_FIELDS: list[FieldSpec] = [
    ("pe_ratio_trailing", "trailingPE", "x"),
    ("pe_ratio_forward", "forwardPE", "x"),
    ("peg_ratio", ("trailingPegRatio", "pegRatio"), "x"),
    ("price_to_book", "priceToBook", "x"),
    ("price_to_sales", "priceToSalesTrailing12Months", "x"),
    ("ev_to_revenue", "enterpriseToRevenue", "x"),
    ("ev_to_ebitda", "enterpriseToEbitda", "x"),
    ("eps_trailing", "trailingEps", _CUR),
    ("eps_forward", "forwardEps", _CUR),
    ("beta", "beta", ""),
    ("gross_margin", "grossMargins", "fraction"),
    ("operating_margin", "operatingMargins", "fraction"),
    ("ebitda_margin", "ebitdaMargins", "fraction"),
    ("profit_margin", "profitMargins", "fraction"),
    ("return_on_equity", "returnOnEquity", "fraction"),
    ("return_on_assets", "returnOnAssets", "fraction"),
    ("revenue_growth_yoy", "revenueGrowth", "fraction"),
    ("earnings_growth_yoy", "earningsGrowth", "fraction"),
    ("debt_to_equity", "debtToEquity", "%"),
    ("current_ratio", "currentRatio", "x"),
    ("quick_ratio", "quickRatio", "x"),
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean(value: object) -> FactValue:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Real):
        number = float(value)
        return None if math.isnan(number) else number
    return str(value)


@ttl_cache(_SLOW_TTL)
def find_ticker(query: str) -> TickerMatch:
    for q in yf.Search(query, max_results=8).quotes:
        if q.get("quoteType") in _QUOTE_TYPES and q.get("symbol"):
            return TickerMatch(
                symbol=q["symbol"],
                name=q.get("shortname") or q.get("longname"),
                asset_type=q.get("quoteType"),
            )
    raise ValueError(f"No listed asset found for '{query}'")


@ttl_cache(_PRICE_TTL)
def _info(ticker: str) -> Mapping[str, object]:
    info = yf.Ticker(ticker).info or {}
    if not (info.get("longName") or info.get("shortName") or info.get("regularMarketPrice")):
        raise ValueError(f"No data found for {ticker}")
    return MappingProxyType(dict(info))


def _from_info(ticker: str, fields: list[FieldSpec], what: str) -> tuple[MarketFact, ...]:
    info = _info(ticker)
    currency = str(info.get("currency") or "USD")
    facts: list[MarketFact] = []
    for metric, keys, unit in fields:
        candidates = (keys,) if isinstance(keys, str) else keys
        value = _clean(next((info[k] for k in candidates if info.get(k) is not None), None))
        if value is None or value == "":
            continue
        facts.append(
            MarketFact(
                ticker=ticker,
                metric=metric,
                value=value,
                unit=currency if unit == _CUR else unit,
                period="current",
                source=f"yahoo_finance.{what}",
                fetched_at=_now(),
            )
        )
    if not facts:
        raise ValueError(f"No {what} data found for {ticker}")
    return tuple(facts)


def price(ticker: str) -> tuple[MarketFact, ...]:
    return _from_info(ticker, PRICE_FIELDS, "price")


def ratios(ticker: str) -> tuple[MarketFact, ...]:
    return _from_info(ticker, RATIO_FIELDS, "ratios")


@ttl_cache(_NEWS_TTL)
def company_news(ticker: str, limit: int = 5) -> tuple[NewsItem, ...]:
    found = yf.Search(ticker, max_results=1, news_count=limit * 4).news or []
    items = [n for n in found if ticker in (n.get("relatedTickers") or [])] or found
    if not items:
        raise ValueError(f"No recent news found for {ticker}")
    items.sort(key=lambda n: n.get("providerPublishTime") or 0, reverse=True)
    return tuple(
        NewsItem(
            ticker=ticker,
            title=n.get("title"),
            publisher=n.get("publisher"),
            url=n.get("link"),
            published=(
                datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")
                if isinstance(t := n.get("providerPublishTime"), int)
                else None
            ),
            source="yahoo_finance.news",
            fetched_at=_now(),
        )
        for n in items[:limit]
    )


@ttl_cache(_NEWS_TTL)
def general_news(limit: int = 10) -> tuple[NewsItem, ...]:
    # yfinance exposes Yahoo Finance search but no trending-tickers API.
    found = yf.Search("market news", max_results=1, news_count=limit * 4).news or []
    if not found:
        raise ValueError("No recent general market news found")
    found.sort(key=lambda n: n.get("providerPublishTime") or 0, reverse=True)
    return tuple(
        NewsItem(
            ticker=next(iter(n.get("relatedTickers") or []), "MARKET"),
            title=n.get("title"),
            publisher=n.get("publisher"),
            url=n.get("link"),
            published=(
                datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")
                if isinstance(t := n.get("providerPublishTime"), int)
                else None
            ),
            source="yahoo_finance.news",
            fetched_at=_now(),
        )
        for n in found[:limit]
    )


def ticker_data(ticker: str) -> TickerData:
    fetchers: tuple[tuple[DataPart, Callable[[str], tuple[MarketFact, ...]]], ...] = (
        ("price", price),
        ("ratios", ratios),
    )
    facts: list[MarketFact] = []
    news: tuple[NewsItem, ...] = ()
    errors: list[DataError] = []
    for part, fetch in fetchers:
        try:
            facts += fetch(ticker)
        except Exception as e:
            errors.append(DataError(part=part, message=f"{type(e).__name__}: {e}"))
    try:
        news = company_news(ticker)
    except Exception as e:
        errors.append(DataError(part="news", message=f"{type(e).__name__}: {e}"))
    return TickerData(ticker=ticker, facts=tuple(facts), news=news, errors=tuple(errors))
