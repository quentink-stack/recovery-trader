"""Transparent GICS-sector benchmarks for S&P 500 ticker research."""

from __future__ import annotations

from recovery_trader.domain.screener import WatchlistItem


GICS_SECTOR_ETFS = {
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Information Technology": "XLK",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

# Frozen from the older-period first quartile in the November 10, 2026
# sector-relative experiment. This is an exploratory risk boundary, not a
# validated probability or a positive recovery signal.
EXTREME_SECTOR_EXCESS_CUTOFF_PP = -7.354168958370034
SECTOR_RELATIVE_PREDICTIVE_CONFIDENCE = 40


def sector_benchmark_for_ticker(
    ticker: str,
    constituents: list[WatchlistItem],
) -> tuple[str | None, str | None]:
    """Return the saved GICS sector and its comparison ETF when available."""
    normalized = ticker.strip().upper()
    item = next((candidate for candidate in constituents if candidate.ticker == normalized), None)
    if item is None or not item.sector:
        return None, None
    return item.sector, GICS_SECTOR_ETFS.get(item.sector)
