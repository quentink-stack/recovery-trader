"""Orchestration for collecting one ticker's research evidence."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Callable

from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.integrations.news import NewsClient
from recovery_trader.integrations.sec_edgar import CompanyProfile, EarningsFacts, EarningsRelease, SecEdgarClient, SecEdgarError, SecFiling
from recovery_trader.research.context import EARNINGS_RECOVERY_WEIGHT, EarningsEvidence, ResearchContext, build_research_context
from recovery_trader.research.earnings import EarningsBrief, build_earnings_brief

ResearchStageCallback = Callable[[str], None]


def _report_stage(callback: ResearchStageCallback | None, stage: str) -> None:
    if callback is not None:
        callback(stage)


class ResearchService:
    def __init__(
        self,
        market_data: AlpacaMarketData,
        news_client: NewsClient,
        sec_client: SecEdgarClient | None = None,
        sec_setup_error: str | None = None,
    ) -> None:
        self.market_data = market_data
        self.news_client = news_client
        self.sec_client = sec_client
        self.sec_setup_error = sec_setup_error

    def collect(
        self,
        ticker: str,
        *,
        as_of: date | None = None,
        lookback_bars: int = 30,
        news_limit: int = 10,
        on_stage: ResearchStageCallback | None = None,
    ) -> ResearchContext:
        """Fetch market/news evidence and return one normalized research context."""
        research_date = as_of or date.today()
        _report_stage(on_stage, "Fetching adjusted price history from Alpaca")
        bars = self.market_data.daily_bars(ticker, research_date - timedelta(days=lookback_bars * 2), research_date)
        _report_stage(on_stage, "Fetching recent news")
        articles = self.news_client.recent_articles(ticker, limit=news_limit)
        article_enricher = getattr(self.news_client, "enrich_articles", None)
        if callable(article_enricher) and articles:
            _report_stage(on_stage, "Reading accessible news articles")
            articles = article_enricher(articles)
        earnings = self._collect_earnings(ticker, research_date, on_stage)
        _report_stage(on_stage, "Preparing evidence")
        return build_research_context(ticker, bars, articles, as_of=research_date, lookback_bars=lookback_bars, earnings=earnings)

    def collect_earnings_preview(
        self,
        ticker: str,
        *,
        as_of: date | None = None,
        on_stage: ResearchStageCallback | None = None,
    ) -> EarningsEvidence | None:
        """Collect SEC-only evidence without market, news, or model work."""
        return self._collect_earnings(ticker, as_of or date.today(), on_stage)

    def historical_earnings_briefs(
        self,
        ticker: str,
        start: date,
        end: date,
        *,
        on_stage: ResearchStageCallback | None = None,
    ) -> tuple[EarningsBrief, ...]:
        """Build historical briefs using each Item 2.02 filing date as availability."""
        if self.sec_setup_error:
            raise ValueError(self.sec_setup_error)
        if self.sec_client is None:
            raise ValueError("SEC earnings collection is not configured.")
        cik = self.sec_client.cik_for_ticker(ticker)
        _report_stage(on_stage, "Fetching SEC filing history")
        filings = self.sec_client.filing_history(cik)
        profile = self.sec_client.company_profile(cik)
        release_filings = sorted(
            (
                filing
                for filing in filings
                if filing.form == "8-K" and "2.02" in filing.items and start <= filing.filing_date <= end
            ),
            key=lambda filing: filing.filing_date,
        )
        _report_stage(on_stage, "Building point-in-time SEC earnings briefs")
        briefs: list[EarningsBrief] = []
        for filing in release_filings:
            facts = self.sec_client.earnings_facts_as_of(cik, filing.filing_date)
            brief = build_earnings_brief(facts, profile, availability_date=filing.filing_date)
            if brief is not None:
                briefs.append(brief)
        return tuple(briefs)

    def _collect_earnings(
        self,
        ticker: str,
        research_date: date,
        on_stage: ResearchStageCallback | None,
    ) -> EarningsEvidence | None:
        if self.sec_setup_error:
            return EarningsEvidence(error=self.sec_setup_error)
        if self.sec_client is None:
            return None
        try:
            _report_stage(on_stage, "Looking up SEC filing identity")
            cik = self.sec_client.cik_for_ticker(ticker)
            _report_stage(on_stage, "Fetching SEC filing history")
            filings = self.sec_client.filing_history(cik)
            _report_stage(on_stage, "Reading SEC company classification")
            profile = self.sec_client.company_profile(cik)
            _report_stage(on_stage, "Fetching structured SEC earnings facts")
            facts = self.sec_client.latest_earnings_facts(cik)
            _report_stage(on_stage, "Finding earnings-release exhibit")
            release = self.sec_client.latest_earnings_release(cik, filings)
            return _build_earnings_evidence(cik, release, facts, profile, filings, research_date)
        except (SecEdgarError, ValueError) as exc:
            return EarningsEvidence(error=f"SEC earnings preview unavailable: {exc}")


def _build_earnings_evidence(
    cik: str,
    release: EarningsRelease | None,
    facts: EarningsFacts | None,
    profile: CompanyProfile | None,
    filings: tuple[SecFiling, ...],
    research_date: date,
) -> EarningsEvidence:
    """Attach timing and evidence confidence without changing SEC GAAP facts."""
    public_release_date = release.filing.filing_date if release else facts.filing_date if facts else None
    days_since_release = None
    if public_release_date is not None:
        days_since_release = max(0, (research_date - public_release_date).days)
    event_freshness = _event_freshness(days_since_release)
    estimated_next_earnings_date = _estimated_next_earnings_date(filings)
    days_until_next_expected_earnings = (
        (estimated_next_earnings_date - research_date).days if estimated_next_earnings_date else None
    )
    raw_data_coverage = _raw_data_coverage(release, facts)
    brief = build_earnings_brief(facts, profile, availability_date=public_release_date or research_date)
    comparable_coverage = brief.comparable_coverage if brief is not None else 0
    evidence_coverage = round(raw_data_coverage * 0.4 + comparable_coverage * 0.6)
    confidence = round(evidence_coverage * event_freshness / 100) if event_freshness is not None else 0
    available_recovery_weight = round(EARNINGS_RECOVERY_WEIGHT * confidence / 100, 2)
    return EarningsEvidence(
        cik=cik,
        release=release,
        facts=facts,
        profile=profile,
        brief=brief,
        public_release_date=public_release_date,
        days_since_release=days_since_release,
        event_freshness=event_freshness,
        estimated_next_earnings_date=estimated_next_earnings_date,
        days_until_next_expected_earnings=days_until_next_expected_earnings,
        raw_data_coverage=raw_data_coverage,
        confidence=confidence,
        available_recovery_weight=available_recovery_weight,
    )


def _event_freshness(days_since_release: int | None) -> int | None:
    """Return a timing confidence factor from public-release age in calendar days."""
    if days_since_release is None:
        return None
    if days_since_release <= 5:
        return 100
    if days_since_release <= 20:
        return 85
    if days_since_release <= 45:
        return 65
    if days_since_release <= 60:
        return 45
    if days_since_release <= 90:
        return 25
    return 10


def _estimated_next_earnings_date(filings: tuple[SecFiling, ...]) -> date | None:
    """Estimate the next event from recent Item 2.02 filing cadence.

    Three events produce two intervals, which avoids presenting a one-interval
    extrapolation as a useful estimate.  Cadences outside a normal quarterly
    range are left unavailable rather than guessed.
    """
    release_dates = sorted(
        {filing.filing_date for filing in filings if filing.form == "8-K" and "2.02" in filing.items},
        reverse=True,
    )
    if len(release_dates) < 3:
        return None
    intervals = [(release_dates[index] - release_dates[index + 1]).days for index in range(len(release_dates) - 1)]
    cadence_days = round(median(intervals))
    if not 60 <= cadence_days <= 130:
        return None
    return release_dates[0] + timedelta(days=cadence_days)


def _raw_data_coverage(release: EarningsRelease | None, facts: EarningsFacts | None) -> int:
    """Measure availability only; reported values themselves are never adjusted."""
    coverage = 20 if release is not None else 0
    if facts is None:
        return coverage
    if facts.revenue is not None:
        coverage += 20
    if facts.net_income is not None:
        coverage += 20
    if facts.eps_diluted is not None:
        coverage += 15
    elif facts.eps_basic is not None:
        coverage += 10
    if facts.cash is not None:
        coverage += 10
    if facts.period_start is not None and facts.period_end is not None and facts.fiscal_period:
        coverage += 15
    return coverage
