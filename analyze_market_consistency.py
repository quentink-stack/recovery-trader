"""Offline robustness checks on an export_market_analysis.py output directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from recovery_trader.domain.market import DailyBar
from recovery_trader.research.consistency import POLICIES, net_return_pct, simulate_exit


FILTERS = ("all_drops", "volatility_2_to_5")
COSTS_BPS = (0, 10, 25)


def prepare_cohort(events: pd.DataFrame, bars: pd.DataFrame):
    """Freeze entry eligibility across exits; purge the full forward window."""
    if bars.duplicated(["ticker", "day"]).any():
        raise ValueError("Duplicate daily bars.")
    if events.duplicated(["ticker", "signal_day"]).any():
        raise ValueError("Duplicate events.")
    for col in ("open", "high", "low", "close"):
        if not bars[col].map(lambda x: math.isfinite(x) and x > 0).all():
            raise ValueError(f"Invalid {col} prices.")
    if ((bars.low > bars[["open", "close"]].min(axis=1)) |
            (bars.high < bars[["open", "close"]].max(axis=1))).any():
        raise ValueError("Inconsistent OHLC range.")
    histories = {
        ticker: [DailyBar(date.fromisoformat(row.day), row.open, row.high, row.low, row.close)
                 for row in group.sort_values("day").itertuples()]
        for ticker, group in bars.groupby("ticker")
    }
    positions = {ticker: {bar.day.isoformat(): i for i, bar in enumerate(history)}
                 for ticker, history in histories.items()}
    spy = {bar.day: bar for bar in histories.get("SPY", [])}
    spy_days = sorted(spy)
    if not spy:
        raise ValueError("SPY bars are required.")
    complete = events[events.complete_30_sessions.astype(str).str.lower().eq("true")].copy()
    dates = sorted(complete.signal_day.unique())
    if len(dates) < 2:
        raise ValueError("Need at least two complete signal dates.")
    boundary = dates[min(len(dates) - 1, max(1, int(len(dates) * 0.7)))]
    counts = {"input_events": len(events), "incomplete": len(events) - len(complete),
              "purged_boundary": 0, "overlapping": 0, "benchmark_gaps": 0}
    cohort = []
    blocked_until = {}
    for row in complete.sort_values(["entry_day", "ticker"]).itertuples():
        history = histories.get(row.ticker, [])
        index = positions.get(row.ticker, {}).get(row.entry_day)
        if index is None or index < 2:
            raise ValueError(f"Missing entry/reference bars for {row.ticker} {row.entry_day}.")
        window = history[index:index + 30]
        if len(window) != 30:
            raise ValueError("An event marked complete lacks 30 bars.")
        prior, signal = history[index - 2:index]
        drop = (signal.close / prior.close - 1) * 100
        if (signal.day.isoformat() != row.signal_day or
                not math.isclose(prior.close, row.prior_close, rel_tol=1e-8) or
                not math.isclose(window[0].open, row.entry_open, rel_tol=1e-8) or
                not math.isclose(drop, row.close_to_close_drop_pct, abs_tol=1e-7)):
            raise ValueError(f"Event disagrees with raw bars: {row.ticker} {row.signal_day}.")
        end_day = window[-1].day.isoformat()
        if row.signal_day < boundary <= end_day:
            counts["purged_boundary"] += 1
            continue
        benchmark_window = [day for day in spy_days if window[0].day <= day <= window[-1].day]
        if [bar.day for bar in window] != benchmark_window:
            counts["benchmark_gaps"] += 1
            continue
        if row.entry_day <= blocked_until.get(row.ticker, ""):
            counts["overlapping"] += 1
            continue
        blocked_until[row.ticker] = end_day
        cohort.append((row, window, "older" if row.signal_day < boundary else "recent"))
    counts["retained_events"] = len(cohort)
    return cohort, spy, boundary, counts


def build_trades(cohort, spy) -> pd.DataFrame:
    rows = []
    for event, window, period in cohort:
        for policy in POLICIES:
            result = simulate_exit(window, event.prior_close, policy)
            exit_day = window[result.index].day
            spy_entry = spy[window[0].day].open
            spy_exit = getattr(spy[exit_day], result.timing)
            rows.append({
                "ticker": event.ticker, "sector": event.sector,
                "signal_day": event.signal_day, "entry_day": event.entry_day,
                "period": period, "quarter": str(pd.Period(event.signal_day, freq="Q")),
                "policy": policy, "drop_volatility_multiple": event.drop_volatility_multiple,
                "exit_day": exit_day.isoformat(), "exit_timing": result.timing,
                "exit_reason": result.reason, "entry_price": window[0].open,
                "exit_price": result.price, "spy_entry_price": spy_entry, "spy_exit_price": spy_exit,
                "sessions_to_exit": result.index + 1,
                "adverse_excursion_pct": result.adverse_excursion_pct,
                **{f"net_return_{cost}bps_pct": net_return_pct(window[0].open, result.price, cost)
                   for cost in COSTS_BPS},
                **{f"excess_return_{cost}bps_pp": (
                    net_return_pct(window[0].open, result.price, cost) -
                    net_return_pct(spy_entry, spy_exit, cost)) for cost in COSTS_BPS},
            })
    if not rows:
        raise ValueError("No eligible non-overlapping events.")
    return pd.DataFrame(rows)


def summarize(group: pd.DataFrame, cost: int) -> dict:
    returns = group[f"net_return_{cost}bps_pct"]
    excess = group[f"excess_return_{cost}bps_pp"]
    date_medians = group.groupby("signal_day")[[f"net_return_{cost}bps_pct",
                                                f"excess_return_{cost}bps_pp"]].median()
    largest_dates = group.signal_day.value_counts().sort_values(ascending=False, kind="stable").head(3).index
    trimmed = group[~group.signal_day.isin(largest_dates)]
    quarterly = group.groupby("quarter")[f"net_return_{cost}bps_pct"].agg(["median", "count"])
    supported_quarters = quarterly[quarterly["count"] >= 30]
    sectors = group.groupby("sector")[f"net_return_{cost}bps_pct"].agg(["median", "count"])
    supported_sectors = sectors[sectors["count"] >= 30]
    return {
        "trades": len(group), "tickers": group.ticker.nunique(), "signal_dates": len(date_medians),
        "win_rate_pct": returns.gt(0).mean() * 100, "mean_net_return_pct": returns.mean(),
        "median_net_return_pct": returns.median(), "p10_net_return_pct": returns.quantile(0.1),
        "worst_net_return_pct": returns.min(), "loss_at_least_10pct_rate": returns.le(-10).mean() * 100,
        "mean_winner_pct": returns[returns.gt(0)].mean(), "mean_loser_pct": returns[returns.le(0)].mean(),
        "median_excess_spy_pp": excess.median(), "mean_excess_spy_pp": excess.mean(),
        "beat_spy_pct": excess.gt(0).mean() * 100,
        "date_balanced_median_return_pct": date_medians.iloc[:, 0].median(),
        "date_balanced_median_excess_spy_pp": date_medians.iloc[:, 1].median(),
        "median_return_without_3_largest_dates_pct": trimmed[f"net_return_{cost}bps_pct"].median(),
        "median_adverse_excursion_pct": group.adverse_excursion_pct.median(),
        "median_sessions_to_exit": group.sessions_to_exit.median(),
        "positive_quarters_min30": int(supported_quarters["median"].gt(0).sum()),
        "quarters_min30": len(supported_quarters),
        "positive_sectors_min30": int(supported_sectors["median"].gt(0).sum()),
        "sectors_min30": len(supported_sectors),
        "sample_flag": "small (<30 trades)" if len(group) < 30 else "descriptive only",
    }


def run_analysis(source: Path, output: Path) -> Path:
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    source_metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    events = pd.read_csv(source / "events.csv")
    bars = pd.read_csv(source / "daily_bars.csv")
    cohort, spy, boundary, counts = prepare_cohort(events, bars)
    trades = build_trades(cohort, spy)
    reports = {"summary": [], "by_sector": [], "by_quarter": []}
    for entry_filter in FILTERS:
        selected = trades if entry_filter == "all_drops" else trades[
            trades.drop_volatility_multiple.ge(2) & trades.drop_volatility_multiple.lt(5)]
        for cost in COSTS_BPS:
            for name, keys in (("summary", ["period", "policy"]),
                               ("by_sector", ["period", "policy", "sector"]),
                               ("by_quarter", ["period", "policy", "quarter"])):
                for values, group in selected.groupby(keys, sort=True):
                    reports[name].append({"entry_filter": entry_filter, "cost_bps_per_side": cost,
                                          **dict(zip(keys, values)), **summarize(group, cost)})
    output.mkdir(parents=True, exist_ok=False)
    trades.to_csv(output / "trades.csv", index=False)
    for name, rows in reports.items():
        pd.DataFrame(rows).to_csv(output / f"{name}.csv", index=False)
    metadata = {
        "source_directory": str(source.resolve()), "recent_signal_start": boundary, **counts,
        "source_feed": source_metadata.get("feed"),
        "minimum_drop_pct": source_metadata.get("minimum_drop_pct"),
        "source_hashes": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                          for name in ("events.csv", "daily_bars.csv", "metadata.json")},
        "policies": list(POLICIES), "entry_filters": list(FILTERS), "cost_bps_per_side": COSTS_BPS,
        "method": [
            "All policies use one common full-30-session cohort; earliest entry retained per ticker, then block 30 sessions.",
            "Older/recent split at 70% of unique complete signal dates; older full windows reaching the boundary are purged before non-overlap selection.",
            "Moderate-volatility filter is 2 <= drop/prior volatility < 5, applied AFTER cohort selection; no threshold search.",
            "Recovery: close >= pre-drop close; stop: close <= 90% of entry; trailing: close <= 90% of highest observed close or entry.",
            "Conditional exits fill NEXT open, including gaps; fixed exits use session-20/30 close. Entry session counts as 1.",
            "SPY uses exactly the same entry open and exit date/open-or-close. Both legs receive identical cost assumptions; mismatched session calendars are excluded.",
            "Date-balanced median is median of per-signal-date medians; it is not a portfolio return.",
            "Quarter/sector breadth only counts groups with >=30 trades; quarter groups may be partial.",
        ],
        "limitations": [
            "Exploratory: the source data has already been inspected. The recent period is NOT an untouched holdout.",
            "No earnings-event confirmation. Current index membership/sector labels introduce survivorship bias.",
            "Different tickers and dates can remain correlated. No independence assumption, significance claims, or fitted ranking.",
            "Not a capital-constrained portfolio; no compounded return, CAGR, or Sharpe. Idle cash after early exits is not modeled.",
            "Adjusted IEX daily bars are approximate research prices, not executable consolidated prices. Observed bars count as sessions.",
            "Stock returns only: no option premiums, implied volatility, spreads, theta, or taxes.",
            "Close-based stops are not hard intraday loss limits. Adverse excursion is worst price vs entry, not portfolio drawdown.",
        ],
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts))
    print(f"Recent check begins: {boundary}; results: {output.resolve()}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing market-analysis export directory")
    parser.add_argument("--output", type=Path, help="New output directory (must not exist)")
    args = parser.parse_args()
    output = args.output or Path(__file__).resolve().parent / "exports" / f"consistency-{datetime.now():%Y%m%d-%H%M%S-%f}"
    run_analysis(args.source, output)


if __name__ == "__main__":
    main()
