"""Compatibility exports for the relocated backtest domain logic."""

from recovery_trader.domain.backtest import BacktestResult, STRATEGIES, Strategy, Trade, run_dip_recovery_proxy, run_strategy
from recovery_trader.domain.market import DailyBar

__all__ = ["BacktestResult", "DailyBar", "STRATEGIES", "Strategy", "Trade", "run_dip_recovery_proxy", "run_strategy"]
