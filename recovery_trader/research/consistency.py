"""Fixed, close-observed exit experiments; no broker or model dependencies."""

from dataclasses import dataclass

from recovery_trader.domain.market import DailyBar


POLICIES = ("hold_20", "hold_30", "recovery_30", "recovery_stop_10", "trailing_close_10")


@dataclass(frozen=True)
class Exit:
    index: int
    price: float
    timing: str
    reason: str
    adverse_excursion_pct: float


def simulate_exit(window: list[DailyBar], prior_close: float, policy: str) -> Exit:
    """Entry is window[0].open; require the same full 30-session cohort.

    Recovery and stop conditions are observed at a daily CLOSE and filled at
    the NEXT open. These are not intraday stop orders. Trailing uses the highest
    observed close (or entry price), not a same-day high with unknown sequencing.
    All policies cap at the scheduled horizon's close, entry session included.
    """
    if policy not in POLICIES:
        raise ValueError(f"Unknown exit policy: {policy}")
    if len(window) != 30:
        raise ValueError("Exactly 30 sessions are required for a comparable cohort.")
    if prior_close <= 0 or window[0].open <= 0:
        raise ValueError("Prices must be positive.")
    horizon = 20 if policy == "hold_20" else 30
    entry = window[0].open
    peak_close = entry
    exit_index, timing, reason = horizon - 1, "close", "time_limit"
    for i in range(horizon - 1):
        close = window[i].close
        peak_close = max(peak_close, close)
        if policy == "recovery_stop_10" and close <= entry * 0.9:
            exit_index, timing, reason = i + 1, "open", "close_stop"
            break
        if policy == "trailing_close_10" and close <= peak_close * 0.9:
            exit_index, timing, reason = i + 1, "open", "trailing_close_stop"
            break
        if policy in ("recovery_30", "recovery_stop_10") and close >= prior_close:
            exit_index, timing, reason = i + 1, "open", "recovery_close"
            break
    price = getattr(window[exit_index], timing)
    # An opening exit does not expose the trade to that session's later low.
    exposed = window[:exit_index + (timing == "close")]
    worst = min(entry, price, *(bar.low for bar in exposed))
    return Exit(exit_index, price, timing, reason, (worst / entry - 1) * 100)


def net_return_pct(entry: float, exit_price: float, cost_bps_per_side: float) -> float:
    """Apply proportional entry/exit friction, not a flat percent subtraction."""
    if entry <= 0 or exit_price <= 0 or not 0 <= cost_bps_per_side < 10000:
        raise ValueError("Invalid price or cost assumption.")
    cost = cost_bps_per_side / 10000
    return (exit_price * (1 - cost) / (entry * (1 + cost)) - 1) * 100
