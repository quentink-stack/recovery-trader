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
    prior_close: float
    signal_close: float
    entry_day: str
    entry_open: float
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
    for index in range(len(bars) - 3, -1, -1):
        prior_day, signal_day, entry_day = bars[index], bars[index + 1], bars[index + 2]
        drop_pct = (signal_day.close / prior_day.close - 1) * 100
        if drop_pct <= -minimum_drop_pct:
            thirty_day_close = close_on_or_after(bars[index + 2:], signal_day.day + timedelta(days=30))
            return DropResearch(
                item.ticker,
                item.company,
                signal_day.day.isoformat(),
                drop_pct,
                prior_day.close,
                signal_day.close,
                entry_day.day.isoformat(),
                entry_day.open,
                close_on_or_after(bars[index + 2:], signal_day.day + timedelta(days=7)),
                thirty_day_close,
                percent_change(signal_day.close, thirty_day_close) if thirty_day_close is not None else None,
            )
    return None


def percent_change(start_price: float, end_price: float | None) -> float | None:
    if end_price is None:
        return None
    return ((end_price - start_price) / start_price) * 100


def close_on_or_after(bars: list[DailyBar], target_day: date) -> float | None:
    return next((bar.close for bar in bars if bar.day >= target_day), None)
