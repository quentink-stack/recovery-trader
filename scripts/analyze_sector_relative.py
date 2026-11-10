"""Test forward outcomes by signal-day return relative to a GICS sector ETF."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
from dataclasses import asdict, fields
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from recovery_trader.domain.market import DailyBar
from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.research.consistency import net_return_pct
from recovery_trader.research.sector_benchmarks import GICS_SECTOR_ETFS
from scripts.analyze_market_consistency import prepare_cohort
from scripts.export_market_analysis import validated_bars

ROOT = Path(__file__).resolve().parents[1]
HORIZONS = (5, 10, 20, 30)
COST_BPS_PER_SIDE = 10
FIXED_BAND_EDGES = (-math.inf, -8.0, -4.0, -1.0, math.inf)
FIXED_BAND_LABELS = (
    "<= -8 pp",
    "(-8, -4] pp",
    "(-4, -1] pp",
    "> -1 pp",
)
QUARTILE_LABELS = (
    "Q1 most negative",
    "Q2",
    "Q3",
    "Q4 least negative / positive",
)


def _percent_return(start: float, end: float) -> float:
    if start <= 0 or end <= 0:
        raise ValueError("Prices must be positive.")
    return (end / start - 1) * 100


def fetch_sector_histories(
    client: AlpacaMarketData,
    sectors: set[str],
    start: date,
    end: date,
) -> dict[str, list[DailyBar]]:
    """Fetch only the sector ETFs needed by the source universe."""
    symbols = tuple(sorted({GICS_SECTOR_ETFS[sector] for sector in sectors if sector in GICS_SECTOR_ETFS}))
    fetched = client.daily_bars_for_symbols(symbols, start, end)
    histories = {
        symbol: validated_bars(fetched.get(symbol, []), start, end)
        for symbol in symbols
    }
    missing = [symbol for symbol, bars in histories.items() if not bars]
    if missing:
        raise ValueError(f"Sector ETF history unavailable: {', '.join(missing)}")
    return histories


def build_sector_events(
    cohort: list,
    stock_bars: pd.DataFrame,
    sector_histories: dict[str, list[DailyBar]],
) -> tuple[pd.DataFrame, int]:
    """Attach point-in-time sector returns and fixed-horizon outcomes."""
    stock_days = {
        ticker: list(group.sort_values("day").day)
        for ticker, group in stock_bars.groupby("ticker")
    }
    stock_positions = {
        ticker: {day: index for index, day in enumerate(days)}
        for ticker, days in stock_days.items()
    }
    sector_by_symbol_day = {
        symbol: {bar.day.isoformat(): bar for bar in bars}
        for symbol, bars in sector_histories.items()
    }
    rows: list[dict] = []
    sector_gaps = 0
    for event, window, period in cohort:
        benchmark = GICS_SECTOR_ETFS.get(event.sector)
        signal_index = stock_positions.get(event.ticker, {}).get(event.signal_day)
        if benchmark is None or signal_index is None or signal_index < 1:
            sector_gaps += 1
            continue
        prior_day = stock_days[event.ticker][signal_index - 1]
        sector_days = sector_by_symbol_day.get(benchmark, {})
        sector_prior = sector_days.get(prior_day)
        sector_signal = sector_days.get(event.signal_day)
        if sector_prior is None or sector_signal is None:
            sector_gaps += 1
            continue
        sector_return = _percent_return(sector_prior.close, sector_signal.close)
        row = {
            "ticker": event.ticker,
            "sector": event.sector,
            "sector_benchmark": benchmark,
            "prior_day": prior_day,
            "signal_day": event.signal_day,
            "entry_day": event.entry_day,
            "period": period,
            "stock_drop_pct": event.close_to_close_drop_pct,
            "sector_return_pct": sector_return,
            "excess_drop_vs_sector_pct": event.close_to_close_drop_pct - sector_return,
            "recovered_prior_close_30_sessions": str(event.recovered_prior_close_30_sessions).lower() == "true",
            "max_drawdown_30_sessions_pct": event.max_drawdown_30_sessions_pct,
        }
        for horizon in HORIZONS:
            row[f"return_{horizon}_sessions_pct"] = net_return_pct(
                window[0].open,
                window[horizon - 1].close,
                COST_BPS_PER_SIDE,
            )
        rows.append(row)
    if not rows:
        raise ValueError("No events had matching sector ETF observations.")
    return pd.DataFrame(rows), sector_gaps


def assign_groups(events: pd.DataFrame) -> tuple[pd.DataFrame, tuple[float, float, float]]:
    """Freeze quartile cutoffs from older feature values, then apply globally."""
    frame = events.copy()
    older = frame.loc[frame.period.eq("older"), "excess_drop_vs_sector_pct"]
    if len(older) < 4:
        raise ValueError("At least four older events are required to define quartiles.")
    cutpoints = tuple(float(value) for value in older.quantile([0.25, 0.5, 0.75]))
    if len(set(cutpoints)) != 3:
        raise ValueError("Older sector-relative values do not define unique quartile cutoffs.")
    frame["quartile"] = pd.cut(
        frame.excess_drop_vs_sector_pct,
        bins=(-math.inf, *cutpoints, math.inf),
        labels=QUARTILE_LABELS,
        include_lowest=True,
    )
    frame["fixed_band"] = pd.cut(
        frame.excess_drop_vs_sector_pct,
        bins=FIXED_BAND_EDGES,
        labels=FIXED_BAND_LABELS,
        include_lowest=True,
    )
    return frame, cutpoints


def _summarize_group(group: pd.DataFrame, horizon: int) -> dict:
    column = f"return_{horizon}_sessions_pct"
    returns = group[column]
    date_medians = group.groupby("signal_day")[column].median()
    return {
        "events": len(group),
        "tickers": group.ticker.nunique(),
        "signal_dates": group.signal_day.nunique(),
        "win_rate_pct": returns.gt(0).mean() * 100,
        "mean_return_pct": returns.mean(),
        "median_return_pct": returns.median(),
        "p10_return_pct": returns.quantile(0.1),
        "worst_return_pct": returns.min(),
        "loss_at_least_10pct_rate": returns.le(-10).mean() * 100,
        "recovery_rate_30_sessions_pct": group.recovered_prior_close_30_sessions.mean() * 100,
        "median_max_drawdown_30_sessions_pct": group.max_drawdown_30_sessions_pct.median(),
        "date_balanced_median_return_pct": date_medians.median(),
        "sample_flag": "small (<30 events)" if len(group) < 30 else "descriptive only",
    }


def summarize_groups(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for grouping, column in (("older-quartile cutoffs", "quartile"), ("fixed bands", "fixed_band")):
        for (period, bucket), group in events.groupby(["period", column], observed=True, sort=True):
            for horizon in HORIZONS:
                rows.append(
                    {
                        "grouping": grouping,
                        "period": period,
                        "bucket": str(bucket),
                        "horizon_sessions": horizon,
                        **_summarize_group(group, horizon),
                    }
                )
    return pd.DataFrame(rows)


def summarize_sectors(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for (period, sector, quartile), group in events.groupby(
        ["period", "sector", "quartile"], observed=True, sort=True
    ):
        rows.append(
            {
                "period": period,
                "sector": sector,
                "quartile": str(quartile),
                **_summarize_group(group, 30),
            }
        )
    return pd.DataFrame(rows)


def _write_sector_bars(path: Path, histories: dict[str, list[DailyBar]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker"] + [field.name for field in fields(DailyBar)])
        writer.writeheader()
        for symbol, bars in histories.items():
            for bar in bars:
                writer.writerow({"ticker": symbol, **asdict(bar)})


def run_analysis(source: Path, output: Path, client: AlpacaMarketData) -> Path:
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    events = pd.read_csv(source / "events.csv")
    bars = pd.read_csv(source / "daily_bars.csv")
    sectors = set(events.sector.dropna())
    fetch_start = date.fromisoformat(str(bars.day.min()))
    fetch_end = date.fromisoformat(metadata["data_end_exclusive"])
    print(f"Fetching {len(sectors)} mapped sector ETF histories; {fetch_start} through {fetch_end} (exclusive).", flush=True)
    sector_histories = fetch_sector_histories(client, sectors, fetch_start, fetch_end)
    cohort, _, boundary, cohort_counts = prepare_cohort(events, bars)
    sector_events, sector_gaps = build_sector_events(cohort, bars, sector_histories)
    grouped_events, cutpoints = assign_groups(sector_events)
    summary = summarize_groups(grouped_events)
    by_sector = summarize_sectors(grouped_events)

    output.mkdir(parents=True, exist_ok=False)
    _write_sector_bars(output / "sector_bars.csv", sector_histories)
    grouped_events.to_csv(output / "events_with_sector.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    by_sector.to_csv(output / "by_sector.csv", index=False)
    result_metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(source.resolve()),
        "source_hashes": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in ("events.csv", "daily_bars.csv", "metadata.json")
        },
        "source_feed": metadata.get("feed"),
        "adjustment": metadata.get("adjustment"),
        "data_end_exclusive": metadata.get("data_end_exclusive"),
        "minimum_drop_pct": metadata.get("minimum_drop_pct"),
        "recent_signal_start": boundary,
        **cohort_counts,
        "sector_benchmark_gaps": sector_gaps,
        "analyzed_events": len(grouped_events),
        "cost_bps_per_side": COST_BPS_PER_SIDE,
        "horizons": HORIZONS,
        "older_quartile_cutpoints_pp": cutpoints,
        "fixed_band_edges_pp": ("-inf", -8.0, -4.0, -1.0, "inf"),
        "sector_benchmarks": GICS_SECTOR_ETFS,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "definitions": [
            "Sector-relative excess is stock signal-day close-to-close return minus matching sector-ETF return.",
            "Quartile cutoffs use older-period feature values only and are applied unchanged to the recent period.",
            "All return horizons enter at day-3 open and exit at the selected session close; entry session is session 1.",
            "Returns apply 10 basis points of friction on entry and exit.",
            "The cohort requires complete 30-session windows, purges the split boundary, and blocks overlapping same-ticker events for 30 sessions.",
        ],
        "limitations": [
            "Exploratory reused data: outcomes were inspected previously; the recent period is not an untouched holdout.",
            "Current S&P 500 membership and current GICS labels introduce survivorship and classification bias.",
            "Sector ETFs are investable proxies, not exact equal-weight peer portfolios; large constituents can influence their own benchmark.",
            "Different tickers and signal dates remain correlated; statistics are descriptive and not significance tests.",
            "Adjusted Alpaca IEX daily bars are research prices, not consolidated executable prices.",
            "Stock returns only; no option premium, spread, implied-volatility, theta, portfolio, or tax modeling.",
        ],
    }
    (output / "metadata.json").write_text(json.dumps(result_metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**cohort_counts, "sector_benchmark_gaps": sector_gaps, "analyzed_events": len(grouped_events)}))
    print(f"Older quartile cutpoints: {cutpoints}")
    print(f"Results: {output.resolve()}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing market-analysis export directory")
    parser.add_argument("--output", type=Path, help="New output directory; it must not already exist")
    args = parser.parse_args()
    output = args.output or ROOT / "exports" / f"sector-relative-{datetime.now():%Y%m%d-%H%M%S-%f}"
    run_analysis(args.source, output, AlpacaMarketData.from_config())


if __name__ == "__main__":
    main()
