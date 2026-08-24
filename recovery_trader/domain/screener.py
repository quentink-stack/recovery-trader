"""Pure rules for identifying sharp one-day equity declines."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from recovery_trader.domain.market import DailyBar


@dataclass(frozen=True)
class WatchlistItem:
    ticker: str
    company: str


@dataclass(frozen=True)
class DropResearch:
    ticker: str
    company: str
    signal_day: str
    drop_pct: float
    signal_close: float
    one_week_close: float | None
    thirty_day_close: float | None
    thirty_day_pct_change: float | None


def load_watchlist(path: Path) -> list[WatchlistItem]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "ticker" not in reader.fieldnames:
            raise ValueError("Watchlist CSV must include a ticker column.")
        return [WatchlistItem(row["ticker"].upper().strip(), row.get("company", "").strip()) for row in reader if row.get("ticker", "").strip()]


def latest_large_drop(item: WatchlistItem, bars: list[DailyBar], minimum_drop_pct: float) -> DropResearch | None:
    for index in range(len(bars) - 2, -1, -1):
        signal, next_day = bars[index], bars[index + 1]
        drop_pct = (next_day.close / signal.close - 1) * 100
        if drop_pct <= -minimum_drop_pct:
            thirty_day_close = close_on_or_after(bars[index + 1:], signal.day + timedelta(days=30))
            return DropResearch(item.ticker, item.company, signal.day.isoformat(), drop_pct, signal.close, close_on_or_after(bars[index + 1:], signal.day + timedelta(days=7)), thirty_day_close, percent_change(signal.close, thirty_day_close) if thirty_day_close is not None else None)
    return None


def percent_change(start_price: float, end_price: float | None) -> float | None:
    if end_price is None:
        return None
    return ((end_price - start_price) / start_price) * 100


def close_on_or_after(bars: list[DailyBar], target_day: date) -> float | None:
    return next((bar.close for bar in bars if bar.day >= target_day), None)
