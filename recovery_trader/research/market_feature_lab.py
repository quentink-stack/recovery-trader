"""Pure helpers for testing market features across symbols and sectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from recovery_trader.domain.market import DailyBar
from recovery_trader.domain.screener import WatchlistItem
from recovery_trader.research.market_features import build_market_feature_history

DISCOVERY_PERIOD = "Discovery (older 70%)"
HOLDOUT_PERIOD = "Holdout (recent 30%)"
INCOMPLETE_PERIOD = "Incomplete 30-session outcome"


@dataclass(frozen=True)
class MarketFeatureObservation:
    ticker: str
    company: str
    sector: str
    signal_day: date
    entry_day: date
    prior_close: float
    entry_open: float
    close_to_close_drop_pct: float
    excess_drop_vs_spy_pct: float | None
    drop_volatility_multiple: float | None
    overnight_gap_pct: float
    intraday_return_pct: float
    return_5_sessions_pct: float | None
    return_10_sessions_pct: float | None
    return_20_sessions_pct: float | None
    return_30_sessions_pct: float | None
    recovered_prior_close_30_sessions: bool | None
    sessions_to_recovery: int | None
    max_drawdown_30_sessions_pct: float | None
    max_favorable_30_sessions_pct: float | None
    complete_30_sessions: bool


@dataclass(frozen=True)
class MarketEventOutcomes:
    """Forward outcomes measured from the day-3 opening price."""

    return_5_sessions_pct: float | None
    return_10_sessions_pct: float | None
    return_20_sessions_pct: float | None
    return_30_sessions_pct: float | None
    recovered_prior_close_30_sessions: bool | None
    sessions_to_recovery: int | None
    max_drawdown_30_sessions_pct: float | None
    max_favorable_30_sessions_pct: float | None
    complete_30_sessions: bool


def chronological_evaluation_periods(
    signal_days: list[date],
    complete_outcomes: list[bool],
) -> tuple[str, ...]:
    """Assign a date-grouped chronological split without same-day leakage."""
    if len(signal_days) != len(complete_outcomes):
        raise ValueError("Signal days and completion flags must have equal lengths.")
    complete_dates = sorted({day for day, complete in zip(signal_days, complete_outcomes) if complete})
    if not complete_dates:
        return tuple(INCOMPLETE_PERIOD for _ in signal_days)
    discovery_date_count = max(1, int(len(complete_dates) * 0.7))
    if len(complete_dates) > 1:
        discovery_date_count = min(discovery_date_count, len(complete_dates) - 1)
    discovery_dates = set(complete_dates[:discovery_date_count])
    return tuple(
        INCOMPLETE_PERIOD if not complete else DISCOVERY_PERIOD if day in discovery_dates else HOLDOUT_PERIOD
        for day, complete in zip(signal_days, complete_outcomes)
    )


def select_sector_sample(
    constituents: list[WatchlistItem],
    sectors: set[str],
    per_sector: int | None,
) -> tuple[WatchlistItem, ...]:
    """Select a stable, distributed sample without favoring ticker alphabet."""
    if per_sector is not None and per_sector < 1:
        raise ValueError("Per-sector sample size must be positive.")
    selected: list[WatchlistItem] = []
    for sector in sorted(sectors):
        members = [item for item in constituents if item.sector == sector]
        if per_sector is not None:
            members = sorted(members, key=lambda item: sha256(item.ticker.encode("utf-8")).digest())[:per_sector]
        selected.extend(members)
    return tuple(sorted(selected, key=lambda item: (item.sector, item.ticker)))


def build_market_feature_observations(
    constituents: tuple[WatchlistItem, ...],
    bars_by_ticker: dict[str, list[DailyBar]],
    benchmark_bars: list[DailyBar],
    minimum_drop_pct: float,
    signal_start: date,
) -> tuple[MarketFeatureObservation, ...]:
    """Calculate every qualifying point-in-time event within the test window."""
    observations: list[MarketFeatureObservation] = []
    for item in constituents:
        ordered_bars = sorted(bars_by_ticker.get(item.ticker, []), key=lambda bar: bar.day)
        bars_by_day = {bar.day: bar for bar in ordered_bars}
        features = build_market_feature_history(
            ordered_bars,
            benchmark_bars,
            minimum_drop_pct,
        )
        for feature in features:
            signal_day = date.fromisoformat(feature.signal_day)
            if signal_day < signal_start:
                continue
            entry_day = date.fromisoformat(feature.entry_day)
            entry_bar = bars_by_day[entry_day]
            outcomes = build_market_event_outcomes(
                ordered_bars,
                entry_day,
                entry_bar.open,
                feature.prior_close,
            )
            observations.append(
                MarketFeatureObservation(
                    ticker=item.ticker,
                    company=item.company,
                    sector=item.sector,
                    signal_day=signal_day,
                    entry_day=entry_day,
                    prior_close=feature.prior_close,
                    entry_open=entry_bar.open,
                    close_to_close_drop_pct=feature.close_to_close_drop_pct,
                    excess_drop_vs_spy_pct=feature.excess_drop_vs_spy_pct,
                    drop_volatility_multiple=feature.drop_volatility_multiple,
                    overnight_gap_pct=feature.overnight_gap_pct,
                    intraday_return_pct=feature.intraday_return_pct,
                    return_5_sessions_pct=outcomes.return_5_sessions_pct,
                    return_10_sessions_pct=outcomes.return_10_sessions_pct,
                    return_20_sessions_pct=outcomes.return_20_sessions_pct,
                    return_30_sessions_pct=outcomes.return_30_sessions_pct,
                    recovered_prior_close_30_sessions=outcomes.recovered_prior_close_30_sessions,
                    sessions_to_recovery=outcomes.sessions_to_recovery,
                    max_drawdown_30_sessions_pct=outcomes.max_drawdown_30_sessions_pct,
                    max_favorable_30_sessions_pct=outcomes.max_favorable_30_sessions_pct,
                    complete_30_sessions=outcomes.complete_30_sessions,
                )
            )
    return tuple(sorted(observations, key=lambda item: (item.signal_day, item.ticker), reverse=True))


def build_market_event_outcomes(
    bars: list[DailyBar],
    entry_day: date,
    entry_open: float,
    recovery_close: float,
    *,
    excursion_sessions: int = 30,
) -> MarketEventOutcomes:
    """Measure forward returns and excursions without inventing missing data.

    A five-session return uses the close of the fifth session beginning with
    the entry session. Recovery requires a daily close at or above the day-1
    reference close. Failed recovery and 30-session excursions are reported
    only when the entire 30-session window is observable.
    """
    if entry_open <= 0 or recovery_close <= 0:
        raise ValueError("Entry and recovery prices must be positive.")
    if excursion_sessions < 1:
        raise ValueError("Excursion sessions must be positive.")

    ordered = sorted(bars, key=lambda bar: bar.day)
    entry_index = next((index for index, bar in enumerate(ordered) if bar.day == entry_day), None)
    if entry_index is None:
        raise ValueError(f"Entry day {entry_day.isoformat()} is missing from the price history.")
    forward = ordered[entry_index:]

    def horizon_return(sessions: int) -> float | None:
        if len(forward) < sessions:
            return None
        return _percent_return(entry_open, forward[sessions - 1].close)

    excursion_window = forward[:excursion_sessions]
    complete_30_sessions = len(excursion_window) == excursion_sessions
    recovery_index = next(
        (index for index, bar in enumerate(excursion_window, start=1) if bar.close >= recovery_close),
        None,
    )
    recovered: bool | None
    if recovery_index is not None:
        recovered = True
    elif complete_30_sessions:
        recovered = False
    else:
        recovered = None

    max_drawdown = None
    max_favorable = None
    if complete_30_sessions:
        max_drawdown = min(0.0, _percent_return(entry_open, min(bar.low for bar in excursion_window)))
        max_favorable = max(0.0, _percent_return(entry_open, max(bar.high for bar in excursion_window)))

    return MarketEventOutcomes(
        return_5_sessions_pct=horizon_return(5),
        return_10_sessions_pct=horizon_return(10),
        return_20_sessions_pct=horizon_return(20),
        return_30_sessions_pct=horizon_return(30),
        recovered_prior_close_30_sessions=recovered,
        sessions_to_recovery=recovery_index,
        max_drawdown_30_sessions_pct=max_drawdown,
        max_favorable_30_sessions_pct=max_favorable,
        complete_30_sessions=complete_30_sessions,
    )


def _percent_return(start: float, end: float) -> float:
    return (end / start - 1) * 100
