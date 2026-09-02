"""Streamlit presentation layer for the Recovery Trader screener."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError

import pandas as pd
import streamlit as st

from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.integrations.news import NewsClient
from recovery_trader.integrations.ollama import OllamaClient
from recovery_trader.integrations.sec_edgar import EarningsFacts, SecEdgarClient
from recovery_trader.domain.screener import latest_large_drop, load_watchlist
from recovery_trader.research.context import EarningsEvidence, ResearchContext
from recovery_trader.research.earnings_backtest import EarningsBriefBacktest, backtest_earnings_briefs
from recovery_trader.research.report import CATEGORY_WEIGHTS, ResearchReport, generate_report
from recovery_trader.research.service import ResearchService

ROOT = Path(__file__).parents[2]
SP500 = ROOT / "data" / "sp500.csv"
SCREEN_DATA_VERSION = "day-two-signal-v1"


def format_elapsed(seconds: float) -> str:
    """Format a stage duration for the research status panel."""
    if seconds < 1:
        return "under 1 second"
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    minutes, remainder = divmod(round(seconds), 60)
    return f"{minutes}m {remainder:02d}s"


class ResearchProgress:
    """Render and persist the live stages of one ticker-research run."""

    def __init__(self, slot: Any, ticker: str, *, ollama_timeout: int) -> None:
        self.ticker = ticker
        self.ollama_timeout = ollama_timeout
        self.events: list[dict[str, str | float]] = []
        self.active_stage: str | None = None
        self.active_started_at: float | None = None
        self.status = slot.status(f"Starting research for {ticker}…", state="running", expanded=True)

    def begin(self, stage: str) -> None:
        self._complete_active_stage()
        self.active_stage = stage
        self.active_started_at = perf_counter()
        timeout_note = f" (configured timeout: {format_elapsed(self.ollama_timeout)})" if stage == "Generating report with local Qwen3" else ""
        self.status.update(label=f"{stage}…{timeout_note}", state="running", expanded=True)

    def complete(self) -> None:
        self._complete_active_stage()
        label = f"Research complete for {self.ticker}"
        self.status.update(label=label, state="complete", expanded=False)
        self._save(state="complete", label=label)

    def fail(self, message: str) -> None:
        stage = self.active_stage or "Research"
        label = f"Research stopped during {stage.lower()}"
        self.status.error(message)
        self.status.update(label=label, state="error", expanded=True)
        self._save(state="error", label=label, error=message, failed_stage=stage)

    def _complete_active_stage(self) -> None:
        if self.active_stage is None or self.active_started_at is None:
            return
        elapsed = perf_counter() - self.active_started_at
        self.events.append({"stage": self.active_stage, "elapsed": elapsed})
        self.status.write(f"✓ {self.active_stage} ({format_elapsed(elapsed)})")
        self.active_stage = None
        self.active_started_at = None

    def _save(self, *, state: str, label: str, error: str | None = None, failed_stage: str | None = None) -> None:
        st.session_state["ticker_research_status"] = {
            "ticker": self.ticker,
            "state": state,
            "label": label,
            "events": self.events,
            "error": error,
            "failed_stage": failed_stage,
        }


def render_saved_research_status(slot: Any) -> None:
    """Restore the final state of the most recent research run after a rerun."""
    saved = st.session_state.get("ticker_research_status")
    if not isinstance(saved, dict):
        return
    state = saved.get("state")
    label = saved.get("label")
    if state not in {"complete", "error"} or not isinstance(label, str):
        return
    status = slot.status(label, state=state, expanded=state == "error")
    for event in saved.get("events", []):
        if isinstance(event, dict) and isinstance(event.get("stage"), str) and isinstance(event.get("elapsed"), (int, float)):
            status.write(f"✓ {event['stage']} ({format_elapsed(float(event['elapsed']))})")
    error = saved.get("error")
    failed_stage = saved.get("failed_stage")
    if isinstance(error, str):
        prefix = f"{failed_stage}: " if isinstance(failed_stage, str) else ""
        status.error(f"{prefix}{error}")

st.set_page_config(page_title="Recovery Trader", page_icon="📉", layout="wide")


@st.cache_resource
def client() -> AlpacaMarketData:
    return AlpacaMarketData.from_config()


@st.cache_resource
def news_client() -> NewsClient:
    return NewsClient()


@st.cache_resource
def ollama_client() -> OllamaClient:
    return OllamaClient()


@st.cache_resource
def sec_edgar_client(user_agent: str, timeout: int) -> SecEdgarClient:
    return SecEdgarClient(user_agent, timeout)


def research_service() -> ResearchService:
    try:
        configured_client = SecEdgarClient.from_config()
        sec_client = sec_edgar_client(configured_client.user_agent, configured_client.timeout)
        return ResearchService(client(), news_client(), sec_client=sec_client)
    except ValueError as exc:
        return ResearchService(client(), news_client(), sec_setup_error=str(exc))


@st.cache_data(ttl="15m", max_entries=16, show_spinner=False)
def load_daily_bars(tickers: tuple[str, ...], start: date, end: date, data_version: str) -> dict[str, list]:
    """Load a versioned data set; data_version is part of the Streamlit cache key."""
    return client().daily_bars_for_symbols(tickers, start, end)


def validate_earnings_briefs(
    ticker: str,
    start: date,
    end: date,
    hold_sessions: int,
) -> tuple[tuple, EarningsBriefBacktest]:
    """Run an availability-date-safe validation without involving Qwen.

    SEC responses are already retained by the cached client resource; keeping
    rich evidence objects out of Streamlit's pickle cache avoids serialization
    failures during a validation run.
    """
    service = research_service()
    briefs = service.historical_earnings_briefs(ticker, start, end)
    bars = client().daily_bars(ticker, start, end)
    return briefs, backtest_earnings_briefs(briefs, bars, hold_sessions=hold_sessions)


def user_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"Alpaca returned HTTP {exc.code}. Check your paper API key, secret, and selected feed."
    if isinstance(exc, URLError):
        return "Could not reach Alpaca. Check your network connection and DNS settings."
    return str(exc)


def configure_sidebar() -> float:
    with st.sidebar:
        st.header("Research controls")
        min_drop = st.slider("Minimum next-day close drop", min_value=2.0, max_value=20.0, value=5.0, step=0.5, format="%.1f%%")
        st.divider()
        st.caption("Data mode: Alpaca Basic — IEX equities. Read-only; no order endpoints are used.")
    return min_drop


def screen_page(min_drop: float) -> None:
    st.title("Large single-day drop screener")
    st.caption("Detects a qualifying close-to-close decline on day 2 and models the earliest entry at day 3's open.")
    universe_name = "S&P 500"
    days = st.select_slider("Lookback", options=[60, 90, 120, 180, 252], value=120, format_func=lambda value: f"{value} calendar days")
    universe_path = SP500
    button_label = "Screen S&P 500"
    if st.button(button_label, type="primary"):
        try:
            constituents = load_watchlist(universe_path)
            if not constituents:
                raise ValueError(f"{universe_path.name} does not contain any tickers.")
            research = []
            progress = st.progress(10, text=f"Loading {len(constituents)} symbols in Alpaca batches…")
            bars_by_ticker = load_daily_bars(tuple(item.ticker for item in constituents), date.today() - timedelta(days=days), date.today(), SCREEN_DATA_VERSION)
            progress.progress(75, text="Screening for qualifying drops…")
            for item in constituents:
                result = latest_large_drop(item, bars_by_ticker.get(item.ticker, []), min_drop)
                if result:
                    research.append(result)
            progress.empty()
            st.session_state["screen_results"] = research
            st.session_state["screen_scope"] = universe_name
            st.session_state["screen_data_version"] = SCREEN_DATA_VERSION
        except Exception as exc:
            st.error(user_error(exc))
    results = st.session_state.get("screen_results", [])
    results_are_current = st.session_state.get("screen_data_version") == SCREEN_DATA_VERSION and all(hasattr(item, "prior_close") and hasattr(item, "entry_day") and hasattr(item, "entry_open") for item in results)
    if results and not results_are_current:
        st.session_state.pop("screen_results", None)
        st.session_state.pop("screen_scope", None)
        st.session_state.pop("screen_data_version", None)
        results = []
        st.info("Saved results used a prior signal format. Run a fresh screen to use day-2 confirmation and day-3 entry timing.")
    if results:
        frame = pd.DataFrame([{
            "Ticker": item.ticker, "Company": item.company, "Signal date": item.signal_day,
            "One-day drop": item.drop_pct, "Prior close": item.prior_close, "Signal close": item.signal_close,
            "Entry date": item.entry_day, "Day-3 entry open": item.entry_open,
            "1-week close": item.one_week_close, "30-day close": item.thirty_day_close,
            "30 day % change": item.thirty_day_pct_change,
        } for item in results]).sort_values("One-day drop")
        st.caption(f"{st.session_state.get('screen_scope', 'Selected universe')}: {len(results)} qualifying symbols. Daily bars are cached for 15 minutes.")
        styled_frame = frame.style.apply(lambda column: ["color: green; font-weight: 600" if pd.notna(value) and value > 0 else "color: red; font-weight: 600" if pd.notna(value) and value < 0 else "" for value in column], subset=["30 day % change"])
        st.dataframe(styled_frame, hide_index=True, column_config={"One-day drop": st.column_config.NumberColumn(format="%.2f%%"), "Prior close": st.column_config.NumberColumn(format="$%.2f"), "Signal close": st.column_config.NumberColumn(format="$%.2f"), "Day-3 entry open": st.column_config.NumberColumn(format="$%.2f"), "1-week close": st.column_config.NumberColumn(format="$%.2f"), "30-day close": st.column_config.NumberColumn(format="$%.2f"), "30 day % change": st.column_config.NumberColumn(format="%.2f%%")})
        st.download_button("Download screener CSV", frame.to_csv(index=False), "drop_screener.csv", "text/csv")
    elif "screen_results" in st.session_state:
        st.info("No S&P 500 ticker had a qualifying drop in that lookback window.")


def display_report(report: ResearchReport) -> None:
    st.subheader(f"{report.ticker} research report")
    score_column, coverage_column, summary_column = st.columns([1, 1, 3])
    with score_column:
        st.metric("Recovery score", f"{report.recovery_score}/100")
    with coverage_column:
        st.metric("Evidence coverage", f"{report.evidence_coverage}/100")
    with summary_column:
        st.write(report.summary)

    st.write("**Category assessments**")
    assessment_frame = pd.DataFrame([
        {
            "Category": category.title(),
            "Weight": f"{CATEGORY_WEIGHTS[category]}%",
            "Coverage": report.category_coverage[category],
            "Rating": assessment.rating.title(),
            "Evidence": assessment.evidence,
        }
        for category, assessment in report.assessments.items()
    ])
    st.dataframe(
        assessment_frame,
        hide_index=True,
        column_config={
            "Coverage": st.column_config.ProgressColumn("Coverage", min_value=0, max_value=100, format="%d%%"),
        },
    )
    st.caption("Recovery score measures evidence-adjusted direction; category coverage pulls weakly supported ratings toward neutral. Evidence coverage measures weighted source completeness. Market (30%) and earnings (25%) carry the most weight.")

    catalyst_column, risk_column, uncertainty_column = st.columns(3)
    with catalyst_column:
        st.write("**Catalysts**")
        for item in report.catalysts:
            st.markdown(f"- {item}")
    with risk_column:
        st.write("**Risks**")
        for item in report.risks:
            st.markdown(f"- {item}")
    with uncertainty_column:
        st.write("**Uncertainties**")
        for item in report.uncertainties:
            st.markdown(f"- {item}")


def _format_sec_metric(value: float | None, *, scale_billions: bool = False) -> str:
    if value is None:
        return "Not reported"
    if scale_billions:
        return f"${value / 1_000_000_000:,.2f}B"
    return f"${value:,.2f}"


def _format_earnings_value(label: str, value: float | None) -> str:
    if value is None:
        return "Not reported"
    if label in {"Basic EPS", "Diluted EPS"}:
        return _format_sec_metric(value)
    if label == "Diluted shares":
        return f"{value / 1_000_000:,.1f}M"
    return _format_sec_metric(value, scale_billions=True)


def display_earnings_preview(earnings: EarningsEvidence | None) -> None:
    st.subheader("SEC earnings data preview")
    st.caption("This compact SEC brief is supplied to Qwen and its freshness-adjusted confidence contributes to both top-level scores.")
    if earnings is None:
        st.info("SEC earnings collection was not configured for this run.")
        return
    if earnings.error:
        st.warning(earnings.error)
        return
    if earnings.cik:
        st.caption(f"CIK: {earnings.cik}")
    if earnings.release is not None:
        release = earnings.release
        st.markdown(
            f"Latest Item 2.02 / EX-99.1 release: [{release.exhibit_name}]({release.exhibit_url}) "
            f"filed {release.filing.filing_date.isoformat()}"
        )
    else:
        st.caption("No Item 2.02 8-K with an EX-99.1 exhibit was found in the recent filing history.")

    timing_rows = [
        {"Measure": "Public release date", "Value": earnings.public_release_date.isoformat() if earnings.public_release_date else "Unavailable"},
        {"Measure": "Days since release", "Value": f"{earnings.days_since_release} calendar days" if earnings.days_since_release is not None else "Unavailable"},
        {"Measure": "Event freshness", "Value": f"{earnings.event_freshness}%" if earnings.event_freshness is not None else "Unavailable"},
        {"Measure": "Estimated next earnings", "Value": earnings.estimated_next_earnings_date.isoformat() if earnings.estimated_next_earnings_date else "Unavailable"},
        {"Measure": "Days until estimated earnings", "Value": _format_days_until(earnings.days_until_next_expected_earnings)},
        {"Measure": "Raw GAAP data coverage", "Value": f"{earnings.raw_data_coverage}%"},
        {"Measure": "Earnings confidence", "Value": f"{earnings.confidence}%"},
        {"Measure": "Available recovery contribution", "Value": f"{earnings.available_recovery_weight:.2f} of 25 points"},
    ]
    st.dataframe(pd.DataFrame(timing_rows), hide_index=True, width="content")
    st.caption(
        "The next earnings date is an estimate from the median interval between recent Item 2.02 filings, not a company-confirmed date. "
        "Event freshness reduces evidence confidence and the available earnings contribution only; it does not modify any raw GAAP result."
    )

    facts = earnings.facts
    if facts is None:
        st.info("No matching 10-Q or 10-K XBRL facts were found.")
        return
    raw_column, brief_column = st.columns(2)
    with raw_column:
        st.write("**Raw SEC metrics**")
        prior = facts.prior_year
        raw_values = (
            ("Revenue", facts.revenue, prior.revenue if prior else None),
            ("Operating income", facts.operating_income, prior.operating_income if prior else None),
            ("Net income", facts.net_income, prior.net_income if prior else None),
            ("Basic EPS", facts.eps_basic, None),
            ("Diluted EPS", facts.eps_diluted, prior.eps_diluted if prior else None),
            ("Operating cash flow", facts.operating_cash_flow, prior.operating_cash_flow if prior else None),
            ("Capex", facts.capex, prior.capex if prior else None),
            ("Debt", facts.debt, prior.debt if prior else None),
            ("Cash", facts.cash, prior.cash if prior else None),
            ("Diluted shares", facts.diluted_shares, prior.diluted_shares if prior else None),
        )
        st.dataframe(
            pd.DataFrame(
                {"Metric": label, "Current": _format_earnings_value(label, current), "Prior-year comparable": _format_earnings_value(label, previous)}
                for label, current, previous in raw_values
            ),
            hide_index=True,
            width="stretch",
        )
    with brief_column:
        st.write("**Deterministic earnings brief**")
        brief = earnings.brief
        if brief is None:
            st.info("A comparable-period brief could not be built from the reported facts.")
        else:
            st.metric("Conclusion", brief.conclusion)
            st.caption(f"Period alignment: {brief.alignment.reason} · comparable coverage: {brief.comparable_coverage}%")
            if brief.sector_exception:
                st.info(brief.sector_exception)
            derived_rows = [
                {
                    "Metric": metric.label,
                    "YoY change": f"{metric.change_pct:+.1f}%" if metric.change_pct is not None else "Unavailable",
                    "Assessment": metric.assessment.title(),
                }
                for metric in brief.metrics
            ]
            st.dataframe(pd.DataFrame(derived_rows), hide_index=True, width="stretch")
            if brief.findings:
                st.caption(" · ".join(brief.findings))
    period_start = facts.period_start.isoformat() if facts.period_start else "not reported"
    fiscal_label = " ".join(part for part in (str(facts.fiscal_year) if facts.fiscal_year else "", facts.fiscal_period or "") if part)
    st.caption(
        f"Source: {facts.form} filed {facts.filing_date.isoformat()} · "
        f"period {period_start} to {facts.period_end.isoformat()} · "
        f"{fiscal_label or 'fiscal period not reported'} · accession {facts.accession_number}"
    )


def display_qwen_evidence_preview(context: ResearchContext) -> None:
    """Show the exact current evidence boundary sent to Qwen."""
    with st.container(border=True):
        st.subheader("Qwen prompt data")
        st.caption("The current JSON below is serialized inside the Qwen prompt exactly as shown.")
        st.write("**Currently sent to Qwen**")
        st.json(context.to_payload(), expanded=False)


def earnings_validation_section() -> None:
    st.header("Validate earnings briefs")
    st.caption("Uses Item 2.02 filing dates as public availability, enters at the following session's open, and never uses same-day information.")
    with st.form("earnings_brief_validation"):
        ticker = st.text_input("Ticker for validation", placeholder="e.g. AAPL").strip().upper()
        hold_sessions = st.slider("Forward holding period", min_value=1, max_value=60, value=10)
        submitted = st.form_submit_button("Run point-in-time validation")
    if not submitted:
        return
    if not ticker:
        st.warning("Enter a ticker symbol first.")
        return
    end = date.today()
    start = end - timedelta(days=365 * 3)
    try:
        with st.spinner("Collecting historical SEC availability dates and Alpaca bars…"):
            briefs, result = validate_earnings_briefs(ticker, start, end, hold_sessions)
    except Exception as exc:
        st.error(user_error(exc))
        return
    st.caption(f"{len(briefs)} SEC briefs considered from {start.isoformat()} through {end.isoformat()}.")
    metric_column, return_column = st.columns(2)
    with metric_column:
        st.metric("Constructive brief trades", len(result.trades))
    with return_column:
        st.metric("Average forward return", f"{result.average_return:.2f}%")
    if result.trades:
        st.dataframe(
            pd.DataFrame(
                {
                    "Availability date": trade.availability_date,
                    "Entry date": trade.entry_day,
                    "Exit date": trade.exit_day,
                    "Return": trade.return_pct,
                    "Conclusion": trade.conclusion,
                }
                for trade in result.trades
            ),
            hide_index=True,
            column_config={"Return": st.column_config.NumberColumn(format="%.2f%%")},
        )
    else:
        st.info("No constructive, period-aligned earnings briefs had a following trading session in this window.")


def _format_days_until(days: int | None) -> str:
    if days is None:
        return "Unavailable"
    if days < 0:
        return f"Overdue by {abs(days)} calendar days"
    return f"{days} calendar days"


def ticker_research_section() -> None:
    st.header("Ticker research")
    st.caption("Combines recent market data and news, then asks the local Qwen3 model for a structured, evidence-grounded assessment.")
    ticker = st.text_input("Ticker to research", placeholder="e.g. AAPL").strip().upper()
    research_requested = st.button("Research ticker", type="primary")
    saved_status = st.session_state.get("ticker_research_status")
    saved_context = st.session_state.get("ticker_research_context")
    retry_available = (
        isinstance(saved_status, dict)
        and saved_status.get("state") == "error"
        and saved_context is not None
        and not isinstance(st.session_state.get("ticker_research_report"), ResearchReport)
    )
    retry_requested = st.button("Retry report generation", icon=":material/refresh:") if retry_available else False
    status_slot = st.container()

    if research_requested:
        if not ticker:
            st.warning("Enter a ticker symbol first.")
        else:
            st.session_state.pop("ticker_research_context", None)
            st.session_state.pop("ticker_research_report", None)
            st.session_state.pop("ticker_research_status", None)
            model_client = ollama_client()
            progress = ResearchProgress(status_slot, ticker, ollama_timeout=model_client.config.timeout)
            try:
                progress.begin("Validating ticker")
                context = research_service().collect(ticker, on_stage=progress.begin)
                st.session_state["ticker_research_context"] = context
                report = generate_report(context, model_client, on_stage=progress.begin)
                st.session_state["ticker_research_report"] = report
                progress.complete()
            except Exception as exc:
                progress.fail(user_error(exc))
                if st.session_state.get("ticker_research_context") is not None:
                    st.rerun()
    elif retry_requested:
        context = saved_context
        model_client = ollama_client()
        progress = ResearchProgress(status_slot, context.ticker, ollama_timeout=model_client.config.timeout)
        try:
            report = generate_report(context, model_client, on_stage=progress.begin)
            st.session_state["ticker_research_report"] = report
            progress.complete()
        except Exception as exc:
            progress.fail(user_error(exc))
    else:
        render_saved_research_status(status_slot)

    report = st.session_state.get("ticker_research_report")
    if isinstance(report, ResearchReport) and hasattr(report, "evidence_coverage"):
        display_report(report)
        context = st.session_state.get("ticker_research_context")
        if isinstance(context, ResearchContext):
            display_qwen_evidence_preview(context)
            display_earnings_preview(context.earnings)
            st.write("**Sources**")
            if context.news:
                for article in context.news:
                    published = article.published_at.date().isoformat() if article.published_at else "date unavailable"
                    st.markdown(f"- [{article.title}]({article.url}) · {article.publisher} · {published}")
            else:
                st.caption("No recent news articles were returned.")
        st.caption("Research output is informational only and is not investment advice.")
    elif report is not None:
        st.session_state.pop("ticker_research_report", None)
        st.info("The saved report used the prior one-score format. Run ticker research again to calculate recovery score and evidence coverage.")


def main() -> None:
    min_drop = configure_sidebar()
    earnings_validation_section()
    st.divider()
    ticker_research_section()
    st.divider()
    page = st.navigation([st.Page(lambda: screen_page(min_drop), title="Drop screener", icon="📉", url_path="screener", default=True)])
    page.run()
