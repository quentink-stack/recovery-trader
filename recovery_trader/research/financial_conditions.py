"""Deterministic, point-in-time NFCI feature construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from recovery_trader.integrations.fred import AlfredClient, VintageObservation


@dataclass(frozen=True)
class FinancialConditionsFeature:
    """NFCI value and four-week change known before an evaluation date."""

    as_of: date
    observation_date: date
    available_date: date
    value: float
    comparison_observation_date: date | None
    comparison_available_date: date | None
    comparison_value: float | None
    four_week_change: float | None
    observation_age_days: int
    release_age_days: int


def _latest_revisions_before(
    observations: Iterable[VintageObservation],
    as_of: date,
) -> dict[date, VintageObservation]:
    """Return the last revision public strictly before ``as_of`` for each week."""
    latest: dict[date, VintageObservation] = {}
    for item in observations:
        if item.realtime_start >= as_of or item.observation_date >= as_of:
            continue
        prior = latest.get(item.observation_date)
        if prior is None or item.realtime_start > prior.realtime_start:
            latest[item.observation_date] = item
    return latest


def build_nfci_feature(
    observations: Iterable[VintageObservation],
    as_of: date,
) -> FinancialConditionsFeature | None:
    """Build the latest NFCI feature that was public before ``as_of``.

    The strict availability boundary is conservative for a modeled entry at
    the market open: a release dated on the entry day is not assumed available.
    """
    revisions = _latest_revisions_before(observations, as_of)
    if not revisions:
        return None
    current = revisions[max(revisions)]
    comparison_candidates = [
        item
        for item in revisions.values()
        if 28 <= (current.observation_date - item.observation_date).days <= 35
    ]
    comparison = max(comparison_candidates, key=lambda item: item.observation_date, default=None)
    change = current.value - comparison.value if comparison is not None else None
    return FinancialConditionsFeature(
        as_of=as_of,
        observation_date=current.observation_date,
        available_date=current.realtime_start,
        value=current.value,
        comparison_observation_date=comparison.observation_date if comparison else None,
        comparison_available_date=comparison.realtime_start if comparison else None,
        comparison_value=comparison.value if comparison else None,
        four_week_change=change,
        observation_age_days=(as_of - current.observation_date).days,
        release_age_days=(as_of - current.realtime_start).days,
    )


def fetch_current_nfci_feature(
    client: AlfredClient,
    as_of: date,
) -> FinancialConditionsFeature | None:
    """Fetch a bounded vintage window and build the current preview feature."""
    observations = client.nfci_realtime_periods(
        observation_start=as_of - timedelta(days=90),
        observation_end=as_of,
        vintage_start=as_of - timedelta(days=120),
        vintage_end=as_of,
    )
    return build_nfci_feature(observations, as_of)
