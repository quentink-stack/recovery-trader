"""Research helpers for identifying sharp one-day equity declines in a watchlist."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from alpaca import DailyBar


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


def load_watchlist(path: Path) -> list[WatchlistItem]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "ticker" not in reader.fieldnames:
            raise ValueError("Watchlist CSV must include a ticker column.")
        return [WatchlistItem(row["ticker"].upper().strip(), row.get("company", "").strip()) for row in reader if row.get("ticker", "").strip()]


def latest_large_drop(item: WatchlistItem, bars: list[DailyBar], minimum_drop_pct: float) -> DropResearch | None:
    for index in range(len(bars) - 1, 0, -1):
        prior, signal = bars[index - 1], bars[index]
        drop_pct = (signal.close / prior.close - 1) * 100
        if drop_pct <= -minimum_drop_pct:
            return DropResearch(
                item.ticker,
                item.company,
                signal.day.isoformat(),
                drop_pct,
                signal.close,
                close_on_or_after(bars[index + 1:], signal.day + timedelta(days=7)),
                close_on_or_after(bars[index + 1:], signal.day + timedelta(days=30)),
            )
    return None


def close_on_or_after(bars: list[DailyBar], target_day: date) -> float | None:
    """Return the close for the first trading session on or after a calendar checkpoint."""
    return next((bar.close for bar in bars if bar.day >= target_day), None)
