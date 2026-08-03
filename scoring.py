"""Pure scoring and screening logic for post-earnings recovery candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Candidate:
    ticker: str
    company: str
    drop_pct: float
    low_hold_pct: float
    iv_percentile: float
    call_open_interest: int
    dte: int
    risk_note: str


@dataclass(frozen=True)
class ScreenConfig:
    min_drop_pct: float = 5.0
    max_iv_percentile: float = 75.0
    min_call_open_interest: int = 500
    min_dte: int = 30
    max_dte: int = 45


def rebound_score(candidate: Candidate) -> int:
    """Return a 0–100 research-priority score; it is not a buy recommendation."""
    dip_score = min(max((candidate.drop_pct - 3.0) / 9.0, 0), 1) * 25
    hold_score = min(max(candidate.low_hold_pct / 100, 0), 1) * 30
    iv_score = (1 - min(max(candidate.iv_percentile / 100, 0), 1)) * 20
    liquidity_score = min(candidate.call_open_interest / 2_500, 1) * 15
    dte_distance = abs(candidate.dte - 38)
    dte_score = max(0, 1 - dte_distance / 20) * 10
    return round(dip_score + hold_score + iv_score + liquidity_score + dte_score)


def passes(candidate: Candidate, config: ScreenConfig) -> bool:
    return (
        candidate.drop_pct >= config.min_drop_pct
        and candidate.iv_percentile <= config.max_iv_percentile
        and candidate.call_open_interest >= config.min_call_open_interest
        and config.min_dte <= candidate.dte <= config.max_dte
    )


def screen(candidates: Iterable[Candidate], config: ScreenConfig) -> list[Candidate]:
    return sorted((item for item in candidates if passes(item, config)), key=rebound_score, reverse=True)
