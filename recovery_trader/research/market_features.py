"""Deterministic market features for reviewing a large-drop event."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import stdev

from recovery_trader.domain.market import DailyBar


@dataclass(frozen=True)
class MarketFeatures:
    """Compact, point-in-time features anchored to one qualifying drop."""

    prior_day: str
    signal_day: str
    entry_day: str
    minimum_drop_pct: float
    prior_close: float
    signal_open: float
    signal_close: float
    close_to_close_drop_pct: float
    spy_return_pct: float | None
    excess_drop_vs_spy_pct: float | None
    prior_daily_volatility_pct: float | None
    drop_volatility_multiple: float | None
    volatility_return_count: int
    overnight_gap_pct: float
    intraday_return_pct: float


def build_market_features(
    bars: list[DailyBar],
    benchmark_bars: list[DailyBar],
    minimum_drop_pct: float,
    *,
    volatility_sessions: int = 20,
) -> MarketFeatures | None:
    """Build features for the latest actionable close-to-close drop.

    The signal must have a following session so the event timing matches the
    screener's day-1 close, day-2 signal close, and day-3 entry convention.
    Volatility uses only returns known through the session before the signal.
    """
    history = build_market_feature_history(
        bars,
        benchmark_bars,
        minimum_drop_pct,
        volatility_sessions=volatility_sessions,
    )
    return history[-1] if history else None


def build_market_feature_history(
    bars: list[DailyBar],
    benchmark_bars: list[DailyBar],
    minimum_drop_pct: float,
    *,
    volatility_sessions: int = 20,
) -> tuple[MarketFeatures, ...]:
    """Return every actionable qualifying event in chronological order."""
    if minimum_drop_pct <= 0:
        raise ValueError("Minimum drop percentage must be positive.")
    if volatility_sessions < 2:
        raise ValueError("Volatility sessions must be at least 2.")

    ordered = sorted(bars, key=lambda bar: bar.day)
    if len(ordered) < 3:
        return ()

    benchmark_by_day = {bar.day: bar for bar in benchmark_bars}
    events: list[MarketFeatures] = []
    for signal_index in range(1, len(ordered) - 1):
        drop_pct = _percent_return(ordered[signal_index - 1].close, ordered[signal_index].close)
        if drop_pct <= -minimum_drop_pct:
            events.append(
                _build_event_features(
                    ordered,
                    benchmark_by_day,
                    signal_index,
                    minimum_drop_pct,
                    volatility_sessions,
                )
            )
    return tuple(events)


def _build_event_features(
    ordered: list[DailyBar],
    benchmark_by_day: dict[date, DailyBar],
    signal_index: int,
    minimum_drop_pct: float,
    volatility_sessions: int,
) -> MarketFeatures:
    prior_bar = ordered[signal_index - 1]
    signal_bar = ordered[signal_index]
    entry_bar = ordered[signal_index + 1]
    drop_pct = _percent_return(prior_bar.close, signal_bar.close)

    benchmark_prior = benchmark_by_day.get(prior_bar.day)
    benchmark_signal = benchmark_by_day.get(signal_bar.day)
    spy_return_pct = None
    excess_drop_vs_spy_pct = None
    if benchmark_prior is not None and benchmark_signal is not None:
        spy_return_pct = _percent_return(benchmark_prior.close, benchmark_signal.close)
        excess_drop_vs_spy_pct = drop_pct - spy_return_pct

    prior_daily_volatility_pct = None
    drop_volatility_multiple = None
    volatility_return_count = 0
    baseline = ordered[max(0, signal_index - volatility_sessions - 1) : signal_index]
    baseline_returns = [
        _percent_return(baseline[index - 1].close, baseline[index].close)
        for index in range(1, len(baseline))
    ]
    volatility_return_count = len(baseline_returns)
    if volatility_return_count == volatility_sessions:
        prior_daily_volatility_pct = stdev(baseline_returns)
        if prior_daily_volatility_pct > 0:
            drop_volatility_multiple = abs(drop_pct) / prior_daily_volatility_pct

    return MarketFeatures(
        prior_day=prior_bar.day.isoformat(),
        signal_day=signal_bar.day.isoformat(),
        entry_day=entry_bar.day.isoformat(),
        minimum_drop_pct=minimum_drop_pct,
        prior_close=prior_bar.close,
        signal_open=signal_bar.open,
        signal_close=signal_bar.close,
        close_to_close_drop_pct=drop_pct,
        spy_return_pct=spy_return_pct,
        excess_drop_vs_spy_pct=excess_drop_vs_spy_pct,
        prior_daily_volatility_pct=prior_daily_volatility_pct,
        drop_volatility_multiple=drop_volatility_multiple,
        volatility_return_count=volatility_return_count,
        overnight_gap_pct=_percent_return(prior_bar.close, signal_bar.open),
        intraday_return_pct=_percent_return(signal_bar.open, signal_bar.close),
    )


def _percent_return(start: float, end: float) -> float:
    if start <= 0:
        raise ValueError("Market prices must be positive.")
    return (end / start - 1) * 100
