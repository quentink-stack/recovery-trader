from unittest import TestCase
from unittest.mock import patch

from refresh_sp500 import fetch_constituents


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class RefreshSp500Tests(TestCase):
    def test_fetch_retains_gics_sector(self) -> None:
        rows = "".join(
            f"<tr><td>T{index}</td><td>Company {index}</td><td>Technology</td></tr>"
            for index in range(500)
        )
        document = (
            '<table id="constituents">'
            "<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>"
            f"{rows}</table>"
        ).encode("utf-8")

        with patch("refresh_sp500.urlopen", return_value=_FakeResponse(document)):
            constituents = fetch_constituents()

        self.assertEqual(len(constituents), 500)
        self.assertEqual(constituents[0], ("T0", "Company 0", "Technology"))
