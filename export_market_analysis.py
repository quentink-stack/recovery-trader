"""Export the market feature lab without starting Streamlit or Ollama."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

from recovery_trader.domain.market import DailyBar
from recovery_trader.domain.screener import load_watchlist
from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.research.market_feature_lab import (
    MarketFeatureObservation,
    build_market_feature_observations,
    chronological_evaluation_periods,
    select_sector_sample,
)

ROOT = Path(__file__).resolve().parent


def validated_bars(bars: list[DailyBar], start: date, end: date) -> list[DailyBar]:
    """Keep closed historical days and reject unusable or duplicate prices."""
    ordered = sorted((bar for bar in bars if start <= bar.day < end), key=lambda bar: bar.day)
    if len({bar.day for bar in ordered}) != len(ordered):
        raise ValueError("Duplicate daily bars returned; export stopped for inspection.")
    for bar in ordered:
        prices = (bar.open, bar.high, bar.low, bar.close)
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise ValueError(f"Invalid OHLC price on {bar.day}; export stopped for inspection.")
    return ordered


def run_export(args: argparse.Namespace, client: AlpacaMarketData) -> Path:
    universe = args.universe.resolve()
    constituents = load_watchlist(universe)
    if args.tickers:
        requested = {symbol.strip().upper() for symbol in args.tickers.split(",") if symbol.strip()}
        unknown = requested - {item.ticker for item in constituents}
        if unknown:
            raise ValueError(f"Tickers absent from the local universe: {', '.join(sorted(unknown))}")
        selected = tuple(item for item in constituents if item.ticker in requested)
    else:
        sectors = {item.sector for item in constituents}
        selected = select_sector_sample(constituents, sectors, args.per_sector)
    if not selected:
        raise ValueError("No tickers selected.")

    # A date-only Alpaca end is exclusive. Never count an unfinished current day.
    end = args.as_of
    signal_start = end - timedelta(days=args.days)
    fetch_start = signal_start - timedelta(days=60)
    output = args.output.resolve() if args.output else (
        ROOT / "exports" / f"market-analysis-{datetime.now():%Y%m%d-%H%M%S-%f}"
    )
    output.mkdir(parents=True, exist_ok=False)
    symbols = tuple(dict.fromkeys([*(item.ticker for item in selected), "SPY"]))
    bars_by_symbol: dict[str, list[DailyBar]] = {}
    print(f"Export directory: {output}", flush=True)
    print(f"Fetching {len(selected)} constituents plus SPY; {fetch_start} through {end} (exclusive).", flush=True)
    for offset in range(0, len(symbols), 50):
        batch = symbols[offset:offset + 50]
        fetched = client.daily_bars_for_symbols(batch, fetch_start, end)
        for symbol in batch:
            bars_by_symbol[symbol] = validated_bars(fetched.get(symbol, []), fetch_start, end)
        print(f"Fetched {min(offset + 50, len(symbols))}/{len(symbols)} symbols (pagination included).", flush=True)
    if not bars_by_symbol.get("SPY"):
        raise ValueError("SPY history is unavailable; export stopped.")

    observations = build_market_feature_observations(
        selected, bars_by_symbol, bars_by_symbol["SPY"], args.min_drop, signal_start
    )
    periods = chronological_evaluation_periods(
        [item.signal_day for item in observations], [item.complete_30_sessions for item in observations]
    )
    event_columns = [field.name for field in fields(MarketFeatureObservation)] + ["evaluation_period"]
    with (output / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=event_columns)
        writer.writeheader()
        for item, period in zip(observations, periods):
            writer.writerow({**asdict(item), "evaluation_period": period})

    # These raw prices allow independent spot checks without another API call.
    with (output / "daily_bars.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker"] + [field.name for field in fields(DailyBar)])
        writer.writeheader()
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                writer.writerow({"ticker": symbol, **asdict(bar)})

    with (output / "universe_coverage.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "company", "sector", "bars", "first_day", "last_day"])
        writer.writeheader()
        for item in selected:
            bars = bars_by_symbol[item.ticker]
            writer.writerow({**asdict(item), "bars": len(bars), "first_day": bars[0].day if bars else None,
                             "last_day": bars[-1].day if bars else None})
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "feed": client.equities_feed,
        "adjustment": "all",
        "signal_start_inclusive": signal_start.isoformat(),
        "data_end_exclusive": end.isoformat(),
        "minimum_drop_pct": args.min_drop,
        "constituents": len(selected),
        "universe_sha256": hashlib.sha256(universe.read_bytes()).hexdigest(),
        "events": len(observations),
        "complete_30_session_events": sum(item.complete_30_sessions for item in observations),
        "symbols_without_bars": [item.ticker for item in selected if not bars_by_symbol[item.ticker]],
        "definitions": {
            "entry": "Day-3 open; entry session counts as session 1.",
            "recovery": "First daily close >= pre-drop close within 30 observed sessions.",
            "max_drawdown_30_sessions_pct": "Worst low versus entry (adverse excursion), not peak-to-trough drawdown.",
            "evaluation_period": "Existing lab split: older 70% of complete signal dates vs recent 30%.",
            "units": "Percent fields use percentage points: 5 means 5%. Missing outcomes are blank in CSV.",
        },
        "limitations": [
            "Uses the saved current constituent list and sectors, not historical index membership.",
            "Overlapping event outcome windows are not purged across the discovery/holdout boundary.",
            "Repeated events in the same ticker and market-wide dates are not independent observations.",
            "Horizons count observed daily bars; feed gaps can lengthen calendar holding periods.",
            "Stock-price outcomes exclude option pricing, transaction costs, and execution slippage.",
        ],
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(observations)} events; {metadata['complete_30_session_events']} complete 30-session outcomes.", flush=True)
    print(f"CSV to share: {output / 'events.csv'}", flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=730, help="Event lookback in calendar days (default: 730).")
    parser.add_argument("--min-drop", type=float, default=5.0)
    parser.add_argument("--per-sector", type=int, help="Optional stable sample size; default is all constituents.")
    parser.add_argument("--tickers", help="Optional comma-separated tickers from the saved universe.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(), help="Exclusive cutoff, YYYY-MM-DD.")
    parser.add_argument("--universe", type=Path, default=ROOT / "data" / "sp500.csv")
    parser.add_argument("--output", type=Path, help="New output directory; existing directories are never overwritten.")
    args = parser.parse_args()
    if args.days < 1 or not math.isfinite(args.min_drop) or args.min_drop <= 0:
        parser.error("Days and minimum drop must be positive finite values.")
    if args.per_sector is not None and args.per_sector < 1:
        parser.error("Per-sector sample size must be positive.")
    if args.tickers and args.per_sector:
        parser.error("Choose either --tickers or --per-sector.")
    if args.as_of > date.today():
        parser.error("The cutoff cannot be in the future.")
    try:
        run_export(args, AlpacaMarketData.from_config())
    except HTTPError as exc:
        parser.exit(1, f"Alpaca returned HTTP {exc.code}; no complete export was produced.\n")
    except (URLError, TimeoutError):
        parser.exit(1, "Alpaca could not be reached or timed out; no complete export was produced.\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")


if __name__ == "__main__":
    main()
