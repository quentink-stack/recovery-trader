"""Research helpers for identifying sharp one-day equity declines in a watchlist."""

from __future__ import annotations

import csv
from dataclasses import dataclass
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
    latest_close: float
    recovery_pct: float


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
            latest_close = bars[-1].close
            return DropResearch(item.ticker, item.company, signal.day.isoformat(), drop_pct, signal.close, latest_close, (latest_close / signal.close - 1) * 100)
    return None
