from datetime import date
from unittest import TestCase

from alpaca import AlpacaMarketData


class AlpacaBatchBarsTests(TestCase):
    def test_multi_symbol_bars_follow_pagination_and_preserve_symbols(self) -> None:
        client = AlpacaMarketData("key", "secret")
        responses = iter([
            {
                "bars": {
                    "AAA": [{"t": "2024-01-02T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5}],
                },
                "next_page_token": "page-2",
            },
            {
                "bars": {
                    "BBB": [{"t": "2024-01-02T00:00:00Z", "o": 20, "h": 21, "l": 19, "c": 20.5}],
                },
            },
        ])
        requests: list[tuple[str, dict]] = []

        def fake_get_json(path: str, params: dict) -> dict:
            requests.append((path, params))
            return next(responses)

        client._get_json = fake_get_json  # type: ignore[method-assign]

        bars = client.daily_bars_for_symbols(["aaa", "BBB"], date(2024, 1, 1), date(2024, 1, 3), batch_size=2)

        self.assertEqual(bars["AAA"][0].close, 10.5)
        self.assertEqual(bars["BBB"][0].close, 20.5)
        self.assertTrue(all(params["adjustment"] == "all" for _, params in requests))
