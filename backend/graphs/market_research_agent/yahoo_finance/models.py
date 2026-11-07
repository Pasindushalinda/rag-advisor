"""Data models for Yahoo Finance results."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

type FactValue = float | str | None
type DataPart = Literal["price", "ratios", "news"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TickerMatch(_Frozen):
    symbol: str
    name: str | None
    asset_type: str | None


class MarketFact(_Frozen):
    ticker: str
    metric: str
    value: FactValue
    unit: str
    period: str
    source: str
    fetched_at: str


class NewsItem(_Frozen):
    ticker: str
    title: str | None
    publisher: str | None
    url: str | None
    published: str | None
    source: str
    fetched_at: str


class GeneralNews(_Frozen):
    items: tuple[NewsItem, ...]


class DataError(_Frozen):
    part: DataPart
    message: str


class TickerData(_Frozen):
    ticker: str
    facts: tuple[MarketFact, ...]
    news: tuple[NewsItem, ...]
    errors: tuple[DataError, ...]
