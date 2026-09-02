from datetime import date
from unittest import TestCase

from recovery_trader.domain.market import DailyBar
from recovery_trader.integrations.sec_edgar import CompanyProfile, ComparableEarningsFacts, EarningsFacts
from recovery_trader.research.earnings_backtest import backtest_earnings_briefs
from recovery_trader.research.earnings import brief_to_payload, build_earnings_brief, period_alignment


def _prior_year(*, fiscal_period: str = "Q2", period_start: date = date(2025, 4, 1)) -> ComparableEarningsFacts:
    return ComparableEarningsFacts(
        "10-Q", date(2025, 7, 30), period_start, date(2025, 6, 30), 2025, fiscal_period,
        100.0, 20.0, 10.0, 1.0, 18.0, 5.0, 60.0, 30.0, 100.0, "prior",
    )


def _current(*, prior_year: ComparableEarningsFacts | None = None) -> EarningsFacts:
    return EarningsFacts(
        "0000000001", "10-Q", date(2026, 7, 30), date(2026, 4, 1), date(2026, 6, 30), 2026, "Q2",
        120.0, 15.0, 1.5, 1.4, 35.0, "current",
        30.0, 24.0, 4.0, 50.0, 95.0, prior_year,
    )


class EarningsBriefTests(TestCase):
    def test_aligned_period_builds_a_constructive_deterministic_brief(self) -> None:
        brief = build_earnings_brief(
            _current(prior_year=_prior_year()),
            CompanyProfile("0000000001", 3571, "Electronic Computers"),
            availability_date=date(2026, 8, 1),
        )

        assert brief is not None
        self.assertTrue(brief.alignment.is_aligned)
        self.assertEqual(brief.conclusion, "Constructive comparable-period trend")
        self.assertEqual(brief.availability_date, date(2026, 8, 1))
        self.assertEqual(brief.comparable_coverage, 100)
        self.assertAlmostEqual(next(metric for metric in brief.metrics if metric.label == "Revenue").change_pct, 20.0)
        self.assertEqual(next(metric for metric in brief.metrics if metric.label == "Debt").assessment, "favorable")

    def test_misaligned_period_refuses_directional_comparison(self) -> None:
        current = _current(prior_year=_prior_year(fiscal_period="Q1"))
        alignment = period_alignment(current, current.prior_year)
        brief = build_earnings_brief(current, None, availability_date=date(2026, 8, 1))

        assert brief is not None
        self.assertFalse(alignment.is_aligned)
        self.assertEqual(brief.conclusion, "Insufficient comparable-period evidence")
        self.assertTrue(all(metric.assessment == "not comparable" for metric in brief.metrics))

    def test_same_filing_prior_year_comparative_is_aligned_by_period_end_gap(self) -> None:
        prior = _prior_year()
        current = _current(prior_year=prior)
        prior_in_current_filing = ComparableEarningsFacts(
            prior.form, date(2026, 7, 30), prior.period_start, prior.period_end, 2026, prior.fiscal_period,
            prior.revenue, prior.operating_income, prior.net_income, prior.eps_diluted, prior.operating_cash_flow,
            prior.capex, prior.debt, prior.cash, prior.diluted_shares, "current",
        )

        alignment = period_alignment(current, prior_in_current_filing)

        self.assertTrue(alignment.is_aligned)
        self.assertIn("period-end gap", alignment.reason)

    def test_financial_sector_excludes_cash_flow_capex_and_debt_from_directional_findings(self) -> None:
        brief = build_earnings_brief(
            _current(prior_year=_prior_year()),
            CompanyProfile("0000000001", 6021, "National Commercial Banks"),
            availability_date=date(2026, 8, 1),
        )

        assert brief is not None
        self.assertIsNotNone(brief.sector_exception)
        assessments = {metric.label: metric.assessment for metric in brief.metrics}
        self.assertEqual(assessments["Operating cash flow"], "sector exception")
        self.assertEqual(assessments["Capex"], "sector exception")
        self.assertEqual(assessments["Debt"], "sector exception")

    def test_point_in_time_backtest_enters_after_the_public_availability_date(self) -> None:
        brief = build_earnings_brief(
            _current(prior_year=_prior_year()),
            CompanyProfile("0000000001", 3571, "Electronic Computers"),
            availability_date=date(2026, 8, 1),
        )
        bars = [
            DailyBar(date(2026, 8, 1), 9, 10, 8, 9),
            DailyBar(date(2026, 8, 4), 10, 11, 9, 10),
            DailyBar(date(2026, 8, 5), 11, 12, 10, 11),
            DailyBar(date(2026, 8, 6), 12, 13, 11, 12),
        ]

        assert brief is not None
        result = backtest_earnings_briefs((brief,), bars, hold_sessions=2)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].availability_date, "2026-08-01")
        self.assertEqual(result.trades[0].entry_day, "2026-08-04")
        self.assertEqual(result.trades[0].exit_day, "2026-08-06")
        self.assertAlmostEqual(result.trades[0].return_pct, 20.0)

    def test_proposed_payload_is_compact_json_data(self) -> None:
        brief = build_earnings_brief(
            _current(prior_year=_prior_year()),
            CompanyProfile("0000000001", 3571, "Electronic Computers"),
            availability_date=date(2026, 8, 1),
        )

        assert brief is not None
        payload = brief_to_payload(brief)

        self.assertEqual(payload["availability_date"], "2026-08-01")
        self.assertEqual(payload["period"], "2026 Q2")
        self.assertTrue(payload["period_alignment"]["is_aligned"])
        self.assertEqual(payload["metrics"][0]["name"], "Revenue")
        self.assertNotIn("filing_html", payload)
