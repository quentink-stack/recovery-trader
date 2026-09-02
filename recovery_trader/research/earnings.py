"""Pure, deterministic interpretation of SEC earnings facts.

The functions here deliberately do not fetch data, invoke a model, or mutate
SEC facts.  They make comparable-period eligibility and sector exceptions
explicit so they can be tested before any model integration is considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from recovery_trader.integrations.sec_edgar import CompanyProfile, ComparableEarningsFacts, EarningsFacts


@dataclass(frozen=True)
class PeriodAlignment:
    is_aligned: bool
    reason: str


@dataclass(frozen=True)
class DerivedMetric:
    label: str
    current: float | None
    prior_year: float | None
    change_pct: float | None
    assessment: str


@dataclass(frozen=True)
class EarningsBrief:
    availability_date: date
    period_label: str
    alignment: PeriodAlignment
    sector_exception: str | None
    comparable_coverage: int
    conclusion: str
    metrics: tuple[DerivedMetric, ...]
    findings: tuple[str, ...]


def period_alignment(current: EarningsFacts, prior_year: ComparableEarningsFacts | None) -> PeriodAlignment:
    """Require an equivalent fiscal period rather than comparing arbitrary filings."""
    if prior_year is None:
        return PeriodAlignment(False, "No prior-year comparable period was reported.")
    if current.form != prior_year.form:
        return PeriodAlignment(False, "Current and prior filings use different forms.")
    if not current.fiscal_period or current.fiscal_period != prior_year.fiscal_period:
        return PeriodAlignment(False, "Current and prior filings use different fiscal periods.")
    fiscal_year_aligned = current.fiscal_year is not None and prior_year.fiscal_year == current.fiscal_year - 1
    period_end_gap = (current.period_end - prior_year.period_end).days
    if not fiscal_year_aligned and not 330 <= period_end_gap <= 400:
        return PeriodAlignment(False, "Current and prior filings are not one fiscal year apart.")
    if current.period_start is not None and prior_year.period_start is not None:
        current_days = (current.period_end - current.period_start).days
        prior_days = (prior_year.period_end - prior_year.period_start).days
        if abs(current_days - prior_days) > 21:
            return PeriodAlignment(False, "Current and prior reporting durations differ materially.")
    if fiscal_year_aligned:
        return PeriodAlignment(True, "Same form and fiscal period, one fiscal year apart.")
    return PeriodAlignment(True, "Same form and fiscal period, with a one-year period-end gap.")


def sector_exception(profile: CompanyProfile | None) -> str | None:
    """Flag industries where standard cash-flow and debt heuristics are unreliable."""
    if profile is not None and profile.sic is not None and 6000 <= profile.sic <= 6799:
        return "Financial-sector exception: operating cash flow, capex, and debt are shown but excluded from directional findings."
    return None


def build_earnings_brief(
    facts: EarningsFacts | None,
    profile: CompanyProfile | None,
    *,
    availability_date: date,
) -> EarningsBrief | None:
    """Derive a compact comparable-period brief without altering raw SEC values."""
    if facts is None:
        return None
    alignment = period_alignment(facts, facts.prior_year)
    exception = sector_exception(profile)
    period_label = " ".join(
        part for part in (str(facts.fiscal_year) if facts.fiscal_year else "", facts.fiscal_period or "") if part
    ) or facts.form
    metrics = _metrics(facts, alignment.is_aligned, exception is not None)
    covered_metrics = sum(metric.current is not None and metric.prior_year is not None for metric in metrics)
    comparable_coverage = round(covered_metrics / len(metrics) * 100) if metrics and alignment.is_aligned else 0
    findings = tuple(_finding(metric) for metric in metrics if metric.assessment in {"favorable", "unfavorable"})
    return EarningsBrief(
        availability_date,
        period_label,
        alignment,
        exception,
        comparable_coverage,
        _conclusion(alignment, metrics),
        metrics,
        findings,
    )


def brief_to_payload(brief: EarningsBrief) -> dict:
    """Return the compact, JSON-serializable form proposed for Qwen evidence."""
    return {
        "availability_date": brief.availability_date.isoformat(),
        "period": brief.period_label,
        "period_alignment": {
            "is_aligned": brief.alignment.is_aligned,
            "reason": brief.alignment.reason,
        },
        "sector_exception": brief.sector_exception,
        "comparable_coverage_percent": brief.comparable_coverage,
        "conclusion": brief.conclusion,
        "metrics": [
            {
                "name": metric.label,
                "current": metric.current,
                "prior_year": metric.prior_year,
                "yoy_change_percent": round(metric.change_pct, 2) if metric.change_pct is not None else None,
                "assessment": metric.assessment,
            }
            for metric in brief.metrics
        ],
        "findings": list(brief.findings),
    }


def _metrics(facts: EarningsFacts, aligned: bool, financial_exception: bool) -> tuple[DerivedMetric, ...]:
    prior = facts.prior_year
    values = (
        ("Revenue", facts.revenue, prior.revenue if prior else None, False, False),
        ("Operating income", facts.operating_income, prior.operating_income if prior else None, False, False),
        ("Net income", facts.net_income, prior.net_income if prior else None, False, False),
        ("Diluted EPS", facts.eps_diluted, prior.eps_diluted if prior else None, False, False),
        ("Operating cash flow", facts.operating_cash_flow, prior.operating_cash_flow if prior else None, False, financial_exception),
        ("Capex", facts.capex, prior.capex if prior else None, True, financial_exception),
        ("Debt", facts.debt, prior.debt if prior else None, True, financial_exception),
        ("Cash", facts.cash, prior.cash if prior else None, False, False),
        ("Diluted shares", facts.diluted_shares, prior.diluted_shares if prior else None, True, False),
    )
    return tuple(_metric(label, current, previous, inverse, skipped, aligned) for label, current, previous, inverse, skipped in values)


def _metric(
    label: str,
    current: float | None,
    previous: float | None,
    inverse: bool,
    skipped: bool,
    aligned: bool,
) -> DerivedMetric:
    if skipped:
        return DerivedMetric(label, current, previous, _change_pct(current, previous), "sector exception")
    if not aligned:
        return DerivedMetric(label, current, previous, None, "not comparable")
    change = _change_pct(current, previous)
    if change is None:
        return DerivedMetric(label, current, previous, None, "insufficient")
    directional_change = -change if inverse else change
    if directional_change >= 5:
        assessment = "favorable"
    elif directional_change <= -5:
        assessment = "unfavorable"
    else:
        assessment = "neutral"
    return DerivedMetric(label, current, previous, change, assessment)


def _change_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current / previous - 1) * 100


def _finding(metric: DerivedMetric) -> str:
    direction = "improved" if metric.assessment == "favorable" else "weakened"
    return f"{metric.label} {direction} {abs(metric.change_pct or 0):.1f}% year over year."


def _conclusion(alignment: PeriodAlignment, metrics: tuple[DerivedMetric, ...]) -> str:
    if not alignment.is_aligned:
        return "Insufficient comparable-period evidence"
    favorable = sum(metric.assessment == "favorable" for metric in metrics)
    unfavorable = sum(metric.assessment == "unfavorable" for metric in metrics)
    observed = favorable + unfavorable + sum(metric.assessment == "neutral" for metric in metrics)
    if observed < 3:
        return "Insufficient reported comparable metrics"
    if favorable >= unfavorable + 2:
        return "Constructive comparable-period trend"
    if unfavorable >= favorable + 2:
        return "Deteriorating comparable-period trend"
    return "Mixed comparable-period trend"
