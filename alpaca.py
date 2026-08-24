"""Compatibility exports for the relocated Alpaca integration."""

from recovery_trader.domain.market import DailyBar
from recovery_trader.integrations.alpaca import AlpacaMarketData

__all__ = ["AlpacaMarketData", "DailyBar"]
