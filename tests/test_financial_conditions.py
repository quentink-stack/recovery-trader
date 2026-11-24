import io
from datetime import date, timedelta
from unittest import TestCase
from urllib.parse import parse_qs
from zipfile import ZipFile

import pandas as pd

from recovery_trader.integrations.fred import (
    AlfredClient,
    VintageObservation,
    parse_observation_bounds,
    parse_realtime_period_zip,
    parse_vintage_dates,
)
from recovery_trader.research.financial_conditions import build_nfci_feature
from scripts.analyze_nfci_regimes import assign_nfci_groups, summarize_groups


class FinancialConditionsTests(TestCase):
    def test_parses_vintage_form_and_realtime_period_archive(self) -> None:
        html = """
        <input id="form_obs_start_date" value="1971-01-08">
        <input id="form_obs_end_date" value="2026-01-09">
        <select id="form_selected_vintage_dates">
          <option value="2026-01-07">2026-01-07</option>
          <option value="not-a-date">invalid</option>
          <option value="2026-01-14">2026-01-14</option>
        </select>
        """
        csv_text = """period_start_date,NFCI,realtime_start_date,realtime_end_date
2025-12-26,-0.50000,2026-01-07,2026-01-13
2026-01-02,-0.48000,2026-01-14,.
"""
        payload = io.BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("README.txt", "fixture")
            archive.writestr("obs._by_real-time_period.csv", csv_text)

        self.assertEqual(parse_vintage_dates(html), (date(2026, 1, 7), date(2026, 1, 14)))
        self.assertEqual(parse_observation_bounds(html), (date(1971, 1, 8), date(2026, 1, 9)))
        observations = parse_realtime_period_zip(payload.getvalue())
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0].value, -0.5)
        self.assertIsNone(observations[1].realtime_end)

    def test_client_clamps_future_observation_end_to_official_bound(self) -> None:
        html = """
        <input id="form_obs_start_date" value="1971-01-08">
        <input id="form_obs_end_date" value="2026-01-09">
        <select id="form_selected_vintage_dates">
          <option value="2026-01-07">2026-01-07</option>
          <option value="2026-01-14">2026-01-14</option>
        </select>
        """
        csv_text = """period_start_date,NFCI,realtime_start_date,realtime_end_date
2026-01-02,-0.48000,2026-01-07,.
"""
        payload = io.BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("obs._by_real-time_period.csv", csv_text)

        class FakeClient(AlfredClient):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            def _request(self, request) -> bytes:
                self.calls += 1
                if self.calls == 1:
                    return html.encode()
                submitted = parse_qs(request.data.decode(), keep_blank_values=True)
                self_outer.assertEqual(submitted["form[obs_end_date]"], ["2026-01-09"])
                return payload.getvalue()

        self_outer = self
        observations = FakeClient().nfci_realtime_periods(
            date(2026, 1, 1),
            date(2026, 1, 20),
            date(2026, 1, 1),
            date(2026, 1, 20),
        )

        self.assertEqual(len(observations), 1)

    def test_feature_uses_only_revisions_available_before_entry(self) -> None:
        observations = (
            VintageObservation(date(2026, 1, 2), -0.50, date(2026, 1, 8), date(2026, 2, 4)),
            VintageObservation(date(2026, 1, 2), -0.40, date(2026, 2, 5), None),
            VintageObservation(date(2026, 1, 30), -0.20, date(2026, 2, 6), None),
        )

        before_release = build_nfci_feature(observations, date(2026, 2, 6))
        after_release = build_nfci_feature(observations, date(2026, 2, 7))

        self.assertEqual(before_release.observation_date, date(2026, 1, 2))
        self.assertIsNone(before_release.four_week_change)
        self.assertEqual(after_release.observation_date, date(2026, 1, 30))
        self.assertAlmostEqual(after_release.four_week_change, 0.20)
        self.assertEqual(after_release.available_date, date(2026, 2, 6))

    def test_analysis_freezes_older_change_quartiles(self) -> None:
        rows = []
        start = date(2026, 1, 1)
        for index, change in enumerate((-0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3)):
            row = {
                "ticker": f"T{index}",
                "signal_day": (start + timedelta(days=index)).isoformat(),
                "period": "older" if index < 4 else "recent",
                "nfci_value": -0.5 + change,
                "nfci_four_week_change": change,
                "stock_drop_pct": -5.0 - index,
                "excess_drop_vs_sector_pct": -4.0,
                "recovered_prior_close_30_sessions": index % 2 == 0,
                "max_drawdown_30_sessions_pct": -8.0,
            }
            for horizon in (5, 10, 20, 30):
                row[f"return_{horizon}_sessions_pct"] = float(index - horizon / 10)
            rows.append(row)

        grouped, cutpoints = assign_nfci_groups(pd.DataFrame(rows))
        summary = summarize_groups(grouped)

        for actual, expected in zip(cutpoints, (-0.325, -0.25, -0.175)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(set(summary.horizon_sessions), {5, 10, 20, 30})
        self.assertEqual(
            set(grouped.nfci_change_direction),
            {"tightening (> 0)", "stable/easing (<= 0)"},
        )
