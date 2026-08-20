"""Read-only Alpaca Basic market-data adapter. This module contains no trading endpoints."""

from __future__ import annotations

import json
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DATA_URL = "https://data.alpaca.markets"


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float


class AlpacaMarketData:
    def __init__(self, api_key: str, api_secret: str, equities_feed: str = "iex") -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.equities_feed = equities_feed

    @classmethod
    def from_config(cls) -> "AlpacaMarketData":
        config = ConfigParser()
        config.read(Path(__file__).with_name("alpaca.ini"), encoding="utf-8")
        if not config.has_section("alpaca"):
            raise ValueError("Create alpaca.ini from the provided template.")
        section = config["alpaca"]
        key, secret = section.get("api_key", ""), section.get("api_secret", "")
        if not key or not secret or key.startswith("PASTE_") or secret.startswith("PASTE_"):
            raise ValueError("Paste Alpaca paper API key and secret into alpaca.ini.")
        return cls(key, secret, section.get("equities_feed", "iex"))

    def _get_json(self, path: str, params: dict[str, str | int]) -> dict:
        request = Request(
            f"{DATA_URL}{path}?{urlencode(params)}",
            headers={"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret, "Accept": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        data = self._get_json(f"/v2/stocks/{ticker.upper()}/bars", {"timeframe": "1Day", "start": start.isoformat(), "end": end.isoformat(), "adjustment": "all", "feed": self.equities_feed, "limit": 10000})
        return [DailyBar(datetime.fromisoformat(item["t"].replace("Z", "+00:00")).date(), float(item["o"]), float(item["h"]), float(item["l"]), float(item["c"])) for item in data.get("bars", [])]

    def daily_bars_for_symbols(self, tickers: list[str] | tuple[str, ...], start: date, end: date, batch_size: int = 100) -> dict[str, list[DailyBar]]:
        """Fetch daily bars in bounded multi-symbol batches, following Alpaca pagination."""
        symbols = tuple(dict.fromkeys(ticker.upper().strip() for ticker in tickers if ticker.strip()))
        results = {symbol: [] for symbol in symbols}
        for offset in range(0, len(symbols), batch_size):
            batch = symbols[offset:offset + batch_size]
            page_token: str | None = None
            while True:
                params: dict[str, str | int] = {
                    "symbols": ",".join(batch),
                    "timeframe": "1Day",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "adjustment": "all",
                    "feed": self.equities_feed,
                    "limit": 10000,
                }
                if page_token:
                    params["page_token"] = page_token
                data = self._get_json("/v2/stocks/bars", params)
                for symbol, raw_bars in data.get("bars", {}).items():
                    normalized_symbol = symbol.upper()
                    results.setdefault(normalized_symbol, []).extend(
                        DailyBar(
                            datetime.fromisoformat(item["t"].replace("Z", "+00:00")).date(),
                            float(item["o"]),
                            float(item["h"]),
                            float(item["l"]),
                            float(item["c"]),
                        )
                        for item in raw_bars
                    )
                page_token = data.get("next_page_token")
                if not page_token:
                    break
        return results
