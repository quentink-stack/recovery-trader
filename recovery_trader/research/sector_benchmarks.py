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
