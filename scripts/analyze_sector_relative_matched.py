"""Match extreme sector-relative drops to similar raw stock declines."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXTREME_CUTOFF_PP = -7.354168958370034
RAW_DROP_CALIPER_PP = 1.0
DATE_CALIPER_DAYS = 180
HORIZONS = (5, 10, 20, 30)
BOOTSTRAP_SAMPLES = 5000
BOOTSTRAP_SEED = 20261110


def _as_bool(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() == "true"


def match_events(events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Create feature-only, same-period/sector pairs without replacement."""
    required = {
        "ticker", "sector", "signal_day", "period", "stock_drop_pct",
        "excess_drop_vs_sector_pct", "recovered_prior_close_30_sessions",
        *(f"return_{horizon}_sessions_pct" for horizon in HORIZONS),
    }
    missing = required - set(events.columns)
    if missing:
        raise ValueError(f"Missing event columns: {', '.join(sorted(missing))}")
    frame = events.copy()
    frame["signal_day"] = pd.to_datetime(frame.signal_day)
    treatments = frame[frame.excess_drop_vs_sector_pct.le(EXTREME_CUTOFF_PP)].copy()
    controls = frame[frame.excess_drop_vs_sector_pct.gt(EXTREME_CUTOFF_PP)].copy()

    eligible: dict[int, list[int]] = {}
    for treatment_index, treatment in treatments.iterrows():
        candidates = controls[
            controls.period.eq(treatment.period)
            & controls.sector.eq(treatment.sector)
            & controls.stock_drop_pct.sub(treatment.stock_drop_pct).abs().le(RAW_DROP_CALIPER_PP)
            & controls.signal_day.sub(treatment.signal_day).abs().dt.days.le(DATE_CALIPER_DAYS)
        ]
        eligible[treatment_index] = list(candidates.index)

    treatment_order = sorted(
        treatments.index,
        key=lambda index: (
            len(eligible[index]),
            treatments.at[index, "signal_day"],
            treatments.at[index, "ticker"],
        ),
    )
    used_controls: set[int] = set()
    rows: list[dict] = []
    matched_treatments: set[int] = set()
    for treatment_index in treatment_order:
        treatment = treatments.loc[treatment_index]
        available = [index for index in eligible[treatment_index] if index not in used_controls]
        if not available:
            continue
        control_index = min(
            available,
            key=lambda index: (
                abs(controls.at[index, "stock_drop_pct"] - treatment.stock_drop_pct),
                abs((controls.at[index, "signal_day"] - treatment.signal_day).days),
                controls.at[index, "ticker"],
                controls.at[index, "signal_day"],
            ),
        )
        control = controls.loc[control_index]
        used_controls.add(control_index)
        matched_treatments.add(treatment_index)
        row = {
            "period": treatment.period,
            "sector": treatment.sector,
            "treatment_ticker": treatment.ticker,
            "treatment_signal_day": treatment.signal_day.date().isoformat(),
            "control_ticker": control.ticker,
            "control_signal_day": control.signal_day.date().isoformat(),
            "calendar_gap_days": abs((control.signal_day - treatment.signal_day).days),
            "treatment_stock_drop_pct": treatment.stock_drop_pct,
            "control_stock_drop_pct": control.stock_drop_pct,
            "absolute_drop_difference_pp": abs(control.stock_drop_pct - treatment.stock_drop_pct),
            "treatment_excess_vs_sector_pp": treatment.excess_drop_vs_sector_pct,
            "control_excess_vs_sector_pp": control.excess_drop_vs_sector_pct,
            "treatment_recovered_30": _as_bool(treatment.recovered_prior_close_30_sessions),
            "control_recovered_30": _as_bool(control.recovered_prior_close_30_sessions),
        }
        for horizon in HORIZONS:
            treatment_return = treatment[f"return_{horizon}_sessions_pct"]
            control_return = control[f"return_{horizon}_sessions_pct"]
            row[f"treatment_return_{horizon}_pct"] = treatment_return
            row[f"control_return_{horizon}_pct"] = control_return
            row[f"paired_difference_{horizon}_pp"] = treatment_return - control_return
        rows.append(row)
    if not rows:
        raise ValueError("No treatment events satisfied the predefined matching rules.")
    counts = {
        "input_events": len(frame),
        "treatment_events": len(treatments),
        "control_events": len(controls),
        "matched_pairs": len(rows),
        "unmatched_treatments": len(treatments) - len(matched_treatments),
    }
    return pd.DataFrame(rows), counts


def _bootstrap_median_interval(values: pd.Series) -> tuple[float, float]:
    array = values.to_numpy(dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    sampled = rng.choice(array, size=(BOOTSTRAP_SAMPLES, len(array)), replace=True)
    medians = np.median(sampled, axis=1)
    return float(np.quantile(medians, 0.025)), float(np.quantile(medians, 0.975))


def _summarize_pairs(group: pd.DataFrame, horizon: int) -> dict:
    treatment = group[f"treatment_return_{horizon}_pct"]
    control = group[f"control_return_{horizon}_pct"]
    difference = group[f"paired_difference_{horizon}_pp"]
    interval = _bootstrap_median_interval(difference)
    return {
        "pairs": len(group),
        "treatment_median_return_pct": treatment.median(),
        "control_median_return_pct": control.median(),
        "median_paired_difference_pp": difference.median(),
        "mean_paired_difference_pp": difference.mean(),
        "paired_median_bootstrap_low_pp": interval[0],
        "paired_median_bootstrap_high_pp": interval[1],
        "treatment_win_rate_pct": treatment.gt(0).mean() * 100,
        "control_win_rate_pct": control.gt(0).mean() * 100,
        "treatment_p10_return_pct": treatment.quantile(0.1),
        "control_p10_return_pct": control.quantile(0.1),
        "treatment_outperformed_control_pct": difference.gt(0).mean() * 100,
        "treatment_recovery_rate_pct": group.treatment_recovered_30.mean() * 100,
        "control_recovery_rate_pct": group.control_recovered_30.mean() * 100,
        "median_absolute_drop_difference_pp": group.absolute_drop_difference_pp.median(),
        "median_calendar_gap_days": group.calendar_gap_days.median(),
        "sample_flag": "small (<30 pairs)" if len(group) < 30 else "descriptive only",
    }


def summarize_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for period in ("older", "recent", "combined"):
        group = pairs if period == "combined" else pairs[pairs.period.eq(period)]
        if group.empty:
            continue
        for horizon in HORIZONS:
            rows.append({"period": period, "horizon_sessions": horizon, **_summarize_pairs(group, horizon)})
    return pd.DataFrame(rows)


def summarize_sectors(pairs: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for (period, sector), group in pairs.groupby(["period", "sector"], sort=True):
        rows.append({"period": period, "sector": sector, **_summarize_pairs(group, 30)})
    return pd.DataFrame(rows)


def run_analysis(source: Path, output: Path) -> Path:
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    events_path = source / "events_with_sector.csv"
    metadata_path = source / "metadata.json"
    events = pd.read_csv(events_path)
    pairs, counts = match_events(events)
    summary = summarize_pairs(pairs)
    by_sector = summarize_sectors(pairs)

    output.mkdir(parents=True, exist_ok=False)
    pairs.to_csv(output / "pairs.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    by_sector.to_csv(output / "by_sector.csv", index=False)
    metadata = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(source.resolve()),
        "source_hashes": {
            "events_with_sector.csv": hashlib.sha256(events_path.read_bytes()).hexdigest(),
            "metadata.json": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        },
        **counts,
        "extreme_cutoff_pp": EXTREME_CUTOFF_PP,
        "raw_drop_caliper_pp": RAW_DROP_CALIPER_PP,
        "date_caliper_days": DATE_CALIPER_DAYS,
        "matching": "Without replacement within period and GICS sector; scarce treatments first; nearest raw drop, date, ticker, signal date.",
        "horizons": HORIZONS,
        "cost_bps_per_side": 10,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "limitations": [
            "Post-hoc exploratory reused data; all outcomes and group patterns were inspected before this matched analysis.",
            "Greedy matching is deterministic but not globally optimal and can leave matchable-looking treatments unmatched.",
            "Matching controls observed raw drop, period, sector, and bounded date only; it does not establish causality.",
            "Bootstrap intervals resample pairs and are not adjusted for correlated signal dates, firms, or sectors.",
            "Current index membership, current sector labels, ETF composition, and IEX pricing limitations remain.",
            "Stock-price outcomes only; no options or portfolio modeling.",
        ],
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts))
    print(f"Results: {output.resolve()}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing sector-relative export directory")
    parser.add_argument("--output", type=Path, help="New output directory; it must not already exist")
    args = parser.parse_args()
    output = args.output or ROOT / "exports" / f"sector-relative-matched-{datetime.now():%Y%m%d-%H%M%S-%f}"
    run_analysis(args.source, output)


if __name__ == "__main__":
    main()
