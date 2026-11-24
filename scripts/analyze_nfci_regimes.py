"""Test point-in-time NFCI regimes against saved large-drop outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from recovery_trader.integrations.fred import AlfredClient, observations_to_rows
from recovery_trader.research.financial_conditions import build_nfci_feature


ROOT = Path(__file__).resolve().parents[1]
HORIZONS = (5, 10, 20, 30)
QUARTILE_LABELS = (
    "Q1 most easing / least tightening",
    "Q2",
    "Q3",
    "Q4 most tightening",
)
BOOTSTRAP_ITERATIONS = 2_000
BOOTSTRAP_SEED = 20261116


def attach_nfci_features(
    events: pd.DataFrame,
    observations: tuple,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Attach the NFCI value known strictly before each event's entry date."""
    frame = events.copy()
    entry_dates = pd.to_datetime(frame.entry_day).dt.date
    features = {
        entry_day: build_nfci_feature(observations, entry_day)
        for entry_day in sorted(set(entry_dates))
    }
    rows: list[dict] = []
    missing_feature = 0
    missing_change = 0
    for row, entry_day in zip(frame.to_dict("records"), entry_dates):
        feature = features[entry_day]
        if feature is None:
            missing_feature += 1
            continue
        if feature.four_week_change is None:
            missing_change += 1
            continue
        rows.append(
            {
                **row,
                "nfci_observation_date": feature.observation_date.isoformat(),
                "nfci_available_date": feature.available_date.isoformat(),
                "nfci_value": feature.value,
                "nfci_comparison_observation_date": feature.comparison_observation_date.isoformat(),
                "nfci_comparison_available_date": feature.comparison_available_date.isoformat(),
                "nfci_comparison_value": feature.comparison_value,
                "nfci_four_week_change": feature.four_week_change,
                "nfci_observation_age_days": feature.observation_age_days,
                "nfci_release_age_days": feature.release_age_days,
            }
        )
    if not rows:
        raise ValueError("No events had a complete point-in-time NFCI feature.")
    return pd.DataFrame(rows), {
        "input_events": len(frame),
        "missing_nfci_feature": missing_feature,
        "missing_nfci_four_week_change": missing_change,
        "analyzed_events": len(rows),
    }


def assign_nfci_groups(events: pd.DataFrame) -> tuple[pd.DataFrame, tuple[float, float, float]]:
    """Freeze four-week-change quartiles from the older period."""
    frame = events.copy()
    older = frame.loc[frame.period.eq("older"), "nfci_four_week_change"]
    if len(older) < 4:
        raise ValueError("At least four older events are required to define NFCI quartiles.")
    cutpoints = tuple(float(value) for value in older.quantile([0.25, 0.5, 0.75]))
    if len(set(cutpoints)) != 3:
        raise ValueError("Older NFCI changes do not define unique quartile cutoffs.")
    frame["nfci_change_quartile"] = pd.cut(
        frame.nfci_four_week_change,
        bins=(-math.inf, *cutpoints, math.inf),
        labels=QUARTILE_LABELS,
        include_lowest=True,
    )
    frame["nfci_change_direction"] = frame.nfci_four_week_change.gt(0).map(
        {True: "tightening (> 0)", False: "stable/easing (<= 0)"}
    )
    return frame, cutpoints


def _summarize_group(group: pd.DataFrame, horizon: int) -> dict:
    returns = group[f"return_{horizon}_sessions_pct"]
    date_medians = group.groupby("signal_day")[f"return_{horizon}_sessions_pct"].median()
    signal_dates = group.signal_day.nunique()
    return {
        "events": len(group),
        "tickers": group.ticker.nunique(),
        "signal_dates": signal_dates,
        "win_rate_pct": returns.gt(0).mean() * 100,
        "mean_return_pct": returns.mean(),
        "median_return_pct": returns.median(),
        "p10_return_pct": returns.quantile(0.1),
        "worst_return_pct": returns.min(),
        "loss_at_least_10pct_rate": returns.le(-10).mean() * 100,
        "recovery_rate_30_sessions_pct": group.recovered_prior_close_30_sessions.mean() * 100,
        "median_max_drawdown_30_sessions_pct": group.max_drawdown_30_sessions_pct.median(),
        "date_balanced_median_return_pct": date_medians.median(),
        "sample_flag": (
            "adequate descriptive sample"
            if len(group) >= 100 and signal_dates >= 20
            else "small (<100 events or <20 signal dates)"
        ),
    }


def summarize_groups(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for grouping, column in (
        ("older-change quartiles", "nfci_change_quartile"),
        ("fixed change direction", "nfci_change_direction"),
    ):
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


def summarize_characteristics(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for (period, bucket), group in events.groupby(
        ["period", "nfci_change_quartile"], observed=True, sort=True
    ):
        rows.append(
            {
                "period": period,
                "bucket": str(bucket),
                "events": len(group),
                "signal_dates": group.signal_day.nunique(),
                "median_nfci": group.nfci_value.median(),
                "median_nfci_four_week_change": group.nfci_four_week_change.median(),
                "median_stock_drop_pct": group.stock_drop_pct.median(),
                "median_sector_excess_pct": group.excess_drop_vs_sector_pct.median(),
                "median_nfci_observation_age_days": group.nfci_observation_age_days.median(),
                "median_nfci_release_age_days": group.nfci_release_age_days.median(),
            }
        )
    return pd.DataFrame(rows)


def _cluster_bootstrap_difference(
    period_events: pd.DataFrame,
    horizon: int,
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Resample whole signal dates and compare Q4 minus Q1 median returns."""
    column = f"return_{horizon}_sessions_pct"
    by_date = {day: group for day, group in period_events.groupby("signal_day")}
    days = sorted(by_date)
    rng = random.Random(seed + horizon)
    differences: list[float] = []
    for _ in range(iterations):
        sampled = [by_date[rng.choice(days)] for _ in days]
        frame = pd.concat(sampled, ignore_index=True)
        low = frame.loc[frame.nfci_change_quartile.eq(QUARTILE_LABELS[0]), column]
        high = frame.loc[frame.nfci_change_quartile.eq(QUARTILE_LABELS[-1]), column]
        if not low.empty and not high.empty:
            differences.append(float(high.median() - low.median()))
    if not differences:
        return math.nan, math.nan
    series = pd.Series(differences)
    return float(series.quantile(0.025)), float(series.quantile(0.975))


def summarize_extreme_comparison(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for period, group in events.groupby("period", sort=True):
        low = group.loc[group.nfci_change_quartile.eq(QUARTILE_LABELS[0])]
        high = group.loc[group.nfci_change_quartile.eq(QUARTILE_LABELS[-1])]
        for horizon in HORIZONS:
            column = f"return_{horizon}_sessions_pct"
            interval_low, interval_high = _cluster_bootstrap_difference(
                group,
                horizon,
                seed=BOOTSTRAP_SEED + (0 if period == "older" else 10_000),
            )
            rows.append(
                {
                    "period": period,
                    "horizon_sessions": horizon,
                    "least_tightening_events": len(low),
                    "most_tightening_events": len(high),
                    "least_tightening_signal_dates": low.signal_day.nunique(),
                    "most_tightening_signal_dates": high.signal_day.nunique(),
                    "least_tightening_median_return_pct": low[column].median(),
                    "most_tightening_median_return_pct": high[column].median(),
                    "most_minus_least_median_return_pp": high[column].median() - low[column].median(),
                    "cluster_bootstrap_95pct_low_pp": interval_low,
                    "cluster_bootstrap_95pct_high_pp": interval_high,
                    "least_tightening_recovery_rate_pct": low.recovered_prior_close_30_sessions.mean() * 100,
                    "most_tightening_recovery_rate_pct": high.recovered_prior_close_30_sessions.mean() * 100,
                    "most_minus_least_recovery_rate_pp": (
                        high.recovered_prior_close_30_sessions.mean()
                        - low.recovered_prior_close_30_sessions.mean()
                    )
                    * 100,
                }
            )
    return pd.DataFrame(rows)


def run_analysis(source: Path, output: Path, client: AlfredClient) -> Path:
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    source_events = source / "events_with_sector.csv"
    source_metadata = source / "metadata.json"
    events = pd.read_csv(source_events)
    if events.empty:
        raise ValueError("Source sector-relative export contains no events.")
    entry_dates = pd.to_datetime(events.entry_day).dt.date
    first_entry = min(entry_dates)
    last_entry = max(entry_dates)
    observations = client.nfci_realtime_periods(
        observation_start=first_entry - timedelta(days=90),
        observation_end=last_entry,
        vintage_start=first_entry - timedelta(days=120),
        vintage_end=last_entry,
    )
    featured, counts = attach_nfci_features(events, observations)
    grouped, cutpoints = assign_nfci_groups(featured)
    summary = summarize_groups(grouped)
    characteristics = summarize_characteristics(grouped)
    comparison = summarize_extreme_comparison(grouped)

    output.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(observations_to_rows(observations)).to_csv(
        output / "nfci_realtime_periods.csv", index=False
    )
    grouped.to_csv(output / "events_with_nfci.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    characteristics.to_csv(output / "group_characteristics.csv", index=False)
    comparison.to_csv(output / "extreme_comparison.csv", index=False)
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(source.resolve()),
        "source_hashes": {
            "events_with_sector.csv": hashlib.sha256(source_events.read_bytes()).hexdigest(),
            "metadata.json": hashlib.sha256(source_metadata.read_bytes()).hexdigest(),
        },
        "source_metadata": json.loads(source_metadata.read_text(encoding="utf-8")),
        **counts,
        "first_entry_date": first_entry.isoformat(),
        "last_entry_date": last_entry.isoformat(),
        "nfci_observations": len(observations),
        "older_nfci_change_quartile_cutpoints": cutpoints,
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "definitions": [
            "NFCI values use ALFRED real-time periods and only revisions public strictly before day-3 entry.",
            "Four-week change uses the latest point-in-time observation 28 to 35 days before the current observation.",
            "Quartile cutoffs use older-period NFCI changes only and are applied unchanged to the recent period.",
            "Bootstrap intervals resample whole signal dates and are descriptive, not causal significance tests.",
        ],
        "limitations": [
            "Exploratory reused stock outcomes; neither period is an untouched holdout.",
            "Weekly NFCI can assign the same feature to many correlated events and signal dates.",
            "Current S&P 500 membership and current GICS labels introduce survivorship and classification bias.",
            "Stock returns only; no option premium, volatility, spread, theta, portfolio, or tax modeling.",
        ],
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts))
    print(f"Older NFCI change quartile cutpoints: {cutpoints}")
    print(f"Results: {output.resolve()}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing sector-relative export directory")
    parser.add_argument("--output", type=Path, help="New output directory; it must not already exist")
    parser.add_argument("--timeout", type=int, default=60, help="ALFRED request timeout in seconds")
    args = parser.parse_args()
    output = args.output or ROOT / "exports" / f"nfci-regimes-{datetime.now():%Y%m%d-%H%M%S-%f}"
    run_analysis(args.source, output, AlfredClient(timeout=args.timeout))


if __name__ == "__main__":
    main()
