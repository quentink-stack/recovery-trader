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

    def test_directory_filename_pattern_identifies_sec_exhibit_when_type_is_not_present(self) -> None:
        filing = _filing("0001-26-000002", date(2026, 8, 1))
        index_url = f"{SEC_ARCHIVES_URL}/320193/000126000002/index.json"
        self.client._get_json = lambda url: {"directory": {"item": [{"name": "a8-kex991q32026.htm", "type": "text.gif"}]}}  # type: ignore[method-assign]

        release = self.client.latest_earnings_release("0000320193", (filing,))

        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release.exhibit_name, "a8-kex991q32026.htm")

    def test_latest_earnings_facts_are_matched_to_one_filing_period(self) -> None:
        cik = "0000320193"
        current_accession = "0000320193-26-000010"
        prior_accession = "0000320193-25-000010"
        current = lambda value, *, start="2026-04-01": _fact("10-Q", "2026-07-30", "2026-06-30", value, start=start, accession=current_accession, fiscal_year=2026)
        prior = lambda value, *, start="2025-04-01": _fact("10-Q", "2025-07-30", "2025-06-30", value, start=start, accession=prior_accession, fiscal_year=2025)
        payload = {"facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [current(95_000_000_000), prior(80_000_000_000)]}},
            "NetIncomeLoss": {"units": {"USD": [current(20_000_000_000), prior(16_000_000_000)]}},
            "EarningsPerShareBasic": {"units": {"USD/shares": [current(1.25), prior(1.0)]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [current(1.24), prior(0.98)]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [current(30_000_000_000, start=None), prior(25_000_000_000, start=None)]}},
            "OperatingIncomeLoss": {"units": {"USD": [current(25_000_000_000), prior(18_000_000_000)]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [current(28_000_000_000), prior(24_000_000_000)]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [current(5_000_000_000), prior(4_000_000_000)]}},
            "LongTermDebt": {"units": {"USD": [current(50_000_000_000, start=None), prior(52_000_000_000, start=None)]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [current(15_000_000_000), prior(16_000_000_000)]}},
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
        self.assertEqual(facts.operating_income, 25_000_000_000.0)
        self.assertEqual(facts.operating_cash_flow, 28_000_000_000.0)
        self.assertEqual(facts.capex, 5_000_000_000.0)
        self.assertEqual(facts.debt, 50_000_000_000.0)
        self.assertEqual(facts.diluted_shares, 15_000_000_000.0)
        self.assertIsNotNone(facts.prior_year)
        self.assertEqual(facts.prior_year.revenue, 80_000_000_000.0)  # type: ignore[union-attr]
        as_of_prior = self.client.earnings_facts_as_of(cik, date(2025, 8, 1))
        self.assertEqual(as_of_prior.filing_date, date(2025, 7, 30))  # type: ignore[union-attr]


def _filing(accession_number: str, filing_date: date):
    from recovery_trader.integrations.sec_edgar import SecFiling

    return SecFiling("0000320193", accession_number, "8-K", filing_date, None, "filing.htm", "Results", ("2.02", "9.01"))


def _fact(
    form: str,
    filed: str,
    end: str,
    value: float,
    *,
    start: str | None = None,
    accession: str = "0000320193-26-000010",
    fiscal_year: int = 2026,
) -> dict:
    payload = {"form": form, "filed": filed, "end": end, "val": value, "accn": accession, "fy": fiscal_year, "fp": "Q2"}
    if start is not None:
        payload["start"] = start
    return payload
