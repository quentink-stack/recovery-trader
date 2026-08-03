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
class AlpacaOption:
    symbol: str
    contract_type: str
    expiration: date
    strike: float
    bid: float | None
    ask: float | None
    last: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None

    @property
    def dte(self) -> int:
        return (self.expiration - date.today()).days

    @property
    def spread(self) -> float | None:
        return round(self.ask - self.bid, 2) if self.bid is not None and self.ask is not None else None


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float


class AlpacaMarketData:
    def __init__(self, api_key: str, api_secret: str, options_feed: str = "indicative", equities_feed: str = "iex") -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.options_feed = options_feed
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
        return cls(key, secret, section.get("options_feed", "indicative"), section.get("equities_feed", "iex"))

    def _get_json(self, path: str, params: dict[str, str | int]) -> dict:
        request = Request(
            f"{DATA_URL}{path}?{urlencode(params)}",
            headers={"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret, "Accept": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def option_chain(self, underlying: str, min_dte: int, max_dte: int) -> list[AlpacaOption]:
        today = date.today()
        data = self._get_json(f"/v1beta1/options/snapshots/{underlying.upper()}", {
            "feed": self.options_feed,
            "expiration_date_gte": today.isoformat(),
            "expiration_date_lte": date.fromordinal(today.toordinal() + max_dte).isoformat(),
            "limit": 1000,
        })
        results: list[AlpacaOption] = []
        for symbol, snapshot in data.get("snapshots", {}).items():
            contract = _parse_occ(symbol)
            if not contract or not min_dte <= (contract.expiration - today).days <= max_dte:
                continue
            quote, trade, greeks = snapshot.get("latestQuote", {}), snapshot.get("latestTrade", {}), snapshot.get("greeks", {})
            results.append(AlpacaOption(symbol, contract.contract_type, contract.expiration, contract.strike, _number(quote.get("bp")), _number(quote.get("ap")), _number(trade.get("p")), _number(greeks.get("delta")), _number(greeks.get("gamma")), _number(greeks.get("theta")), _number(greeks.get("vega"))))
        return sorted(results, key=lambda item: (item.expiration, item.strike, item.contract_type))

    def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        data = self._get_json(f"/v2/stocks/{ticker.upper()}/bars", {"timeframe": "1Day", "start": start.isoformat(), "end": end.isoformat(), "adjustment": "raw", "feed": self.equities_feed, "limit": 10000})
        return [DailyBar(datetime.fromisoformat(item["t"].replace("Z", "+00:00")).date(), float(item["o"]), float(item["h"]), float(item["l"]), float(item["c"])) for item in data.get("bars", [])]


@dataclass(frozen=True)
class _ContractParts:
    expiration: date
    contract_type: str
    strike: float


def _parse_occ(symbol: str) -> _ContractParts | None:
    import re
    match = re.match(r"^.+?(\d{6})([CP])(\d{8})$", symbol)
    if not match: return None
    yymmdd, right, raw_strike = match.groups()
    return _ContractParts(datetime.strptime(yymmdd, "%y%m%d").date(), "CALL" if right == "C" else "PUT", int(raw_strike) / 1000)


def _number(value: object) -> float | None:
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None
