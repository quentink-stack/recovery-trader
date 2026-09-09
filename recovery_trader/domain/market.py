"""Shared market-data value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    trade_count: int | None = None
    vwap: float | None = None
