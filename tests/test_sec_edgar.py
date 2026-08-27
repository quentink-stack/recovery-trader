import json
import os
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from recovery_trader.integrations.sec_edgar import SEC_ARCHIVES_URL, SEC_COMPANY_FACTS_URL, SEC_SUBMISSIONS_URL, SEC_TICKERS_URL, SecEdgarClient


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class SecEdgarClientTests(TestCase):
    def setUp(self) -> None:
        self.client = SecEdgarClient("Recovery Trader tests@example.com")

    def test_cik_lookup_is_normalized_and_cached(self) -> None:
        responses = iter([{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}])
        with patch("recovery_trader.integrations.sec_edgar.urlopen", side_effect=lambda *_args, **_kwargs: FakeResponse(next(responses))) as mocked:
            self.assertEqual(self.client.cik_for_ticker("aapl"), "0000320193")
            self.assertEqual(self.client.cik_for_ticker("AAPL"), "0000320193")

        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(mocked.call_args.args[0].full_url, SEC_TICKERS_URL)
        self.assertIn("tests@example.com", mocked.call_args.args[0].get_header("User-agent"))

    def test_environment_configuration_overrides_local_file(self) -> None:
        with patch.dict(os.environ, {"SEC_USER_AGENT": "Recovery Trader quentin@example.com", "SEC_TIMEOUT": "45"}):
            configured = SecEdgarClient.from_config()

        self.assertEqual(configured.user_agent, "Recovery Trader quentin@example.com")
        self.assertEqual(configured.timeout, 45)

    def test_filing_history_normalizes_recent_submissions(self) -> None:
        payload = {
            "filings": {"recent": {
                "accessionNumber": ["0001-26-000002", "0001-26-000001"],
                "form": ["8-K", "10-Q"],
                "filingDate": ["2026-08-01", "2026-07-20"],
                "reportDate": ["2026-07-31", "2026-06-30"],
                "primaryDocument": ["earnings8k.htm", "q2.htm"],
                "primaryDocDescription": ["Results of Operations", "Quarterly report"],
                "items": ["2.02, 9.01", ""],
            }}
        }
        self.client._get_json = lambda _url: payload  # type: ignore[method-assign]

        filings = self.client.filing_history("320193")

        self.assertEqual(len(filings), 2)
        self.assertEqual(filings[0].cik, "0000320193")
        self.assertEqual(filings[0].items, ("2.02", "9.01"))
        self.assertEqual(filings[1].report_date, date(2026, 6, 30))

    def test_newest_8k_with_exhibit_99_1_is_selected_without_fetching_html(self) -> None:
        first = _filing("0001-26-000003", date(2026, 8, 10))
        second = _filing("0001-26-000002", date(2026, 8, 1))
        archive_cik = "320193"
        first_index = f"{SEC_ARCHIVES_URL}/{archive_cik}/000126000003/index.json"
        second_index = f"{SEC_ARCHIVES_URL}/{archive_cik}/000126000002/index.json"
        responses = {
            first_index: {"directory": {"item": [{"name": "first.htm", "type": "8-K"}]}},
            second_index: {"directory": {"item": [{"name": "earnings.htm", "type": "EX-99.1"}]}},
        }
        requested: list[str] = []

        def get_json(url: str) -> dict:
            requested.append(url)
            return responses[url]

        self.client._get_json = get_json  # type: ignore[method-assign]
        release = self.client.latest_earnings_release("0000320193", (first, second))

        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release.filing.accession_number, second.accession_number)
        self.assertTrue(release.exhibit_url.endswith("/earnings.htm"))
        self.assertEqual(requested, [first_index, second_index])

    def test_latest_earnings_facts_are_matched_to_one_filing_period(self) -> None:
        cik = "0000320193"
        payload = {"facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [_fact("10-Q", "2026-07-30", "2026-06-30", 95_000_000_000, start="2026-04-01")]}},
            "NetIncomeLoss": {"units": {"USD": [_fact("10-Q", "2026-07-30", "2026-06-30", 20_000_000_000, start="2026-04-01")]}},
            "EarningsPerShareBasic": {"units": {"USD/shares": [_fact("10-Q", "2026-07-30", "2026-06-30", 1.25, start="2026-04-01")]}} ,
            "EarningsPerShareDiluted": {"units": {"USD/shares": [_fact("10-Q", "2026-07-30", "2026-06-30", 1.24, start="2026-04-01")]}} ,
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_fact("10-Q", "2026-07-30", "2026-06-30", 30_000_000_000)]}},
        }}}
        requested: list[str] = []
        self.client._get_json = lambda url: (requested.append(url) or payload)  # type: ignore[method-assign]

        facts = self.client.latest_earnings_facts(cik)

        self.assertIsNotNone(facts)
        assert facts is not None
        self.assertEqual(requested, [f"{SEC_COMPANY_FACTS_URL}/CIK{cik}.json"])
        self.assertEqual(facts.form, "10-Q")
        self.assertEqual(facts.period_start, date(2026, 4, 1))
        self.assertEqual(facts.revenue, 95_000_000_000.0)
        self.assertEqual(facts.net_income, 20_000_000_000.0)
        self.assertEqual(facts.eps_basic, 1.25)
        self.assertEqual(facts.eps_diluted, 1.24)
        self.assertEqual(facts.cash, 30_000_000_000.0)


def _filing(accession_number: str, filing_date: date):
    from recovery_trader.integrations.sec_edgar import SecFiling

    return SecFiling("0000320193", accession_number, "8-K", filing_date, None, "filing.htm", "Results", ("2.02", "9.01"))


def _fact(form: str, filed: str, end: str, value: float, *, start: str | None = None) -> dict:
    payload = {"form": form, "filed": filed, "end": end, "val": value, "accn": "0000320193-26-000010", "fy": 2026, "fp": "Q2"}
    if start is not None:
        payload["start"] = start
    return payload
