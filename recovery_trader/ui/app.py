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
from recovery_trader.research.market_feature_lab import (
    build_market_feature_observations,
    chronological_evaluation_periods,
    select_sector_sample,
)
from recovery_trader.research.report import CATEGORY_WEIGHTS, ResearchReport, generate_report
from recovery_trader.research.service import ResearchService

ROOT = Path(__file__).parents[2]
SP500 = ROOT / "data" / "sp500.csv"
SCREEN_DATA_VERSION = "day-two-signal-v1"
MARKET_FEATURE_LAB_VERSION = "market-feature-outcomes-v2"
RESEARCH_PIPELINE_VERSION = "headline-aligned-news-v1"
RESEARCH_SESSION_KEYS = (
    "ticker_research_context",
    "ticker_research_report",
    "ticker_research_status",
)


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


def refresh_stale_research_session() -> None:
    """Discard saved evidence when its collection/validation pipeline changes."""
    version_key = "ticker_research_pipeline_version"
    if st.session_state.get(version_key) == RESEARCH_PIPELINE_VERSION:
        return
    for key in RESEARCH_SESSION_KEYS:
        st.session_state.pop(key, None)
    st.session_state[version_key] = RESEARCH_PIPELINE_VERSION

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


def _assign_evaluation_period(frame: pd.DataFrame) -> pd.DataFrame:
    """Split complete outcomes chronologically while retaining recent unknowns."""
    result = frame.copy()
    result["Evaluation period"] = chronological_evaluation_periods(
        result["Signal date"].tolist(),
        result["30-session complete"].tolist(),
    )
    return result


def _outcome_summary(frame: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    """Summarize comparable outcomes while excluding missing horizons."""
    rows: list[dict[str, Any]] = []
    grouper: str | list[str] = group_columns[0] if len(group_columns) == 1 else group_columns
    for group_key, group in frame.groupby(grouper, observed=True, sort=False):
        keys = (group_key,) if len(group_columns) == 1 else group_key
        complete = group[group["30-session complete"]]
        row = dict(zip(group_columns, keys))
        row.update(
            {
                "Events": len(group),
                "Complete 30-session events": len(complete),
                "Recovery rate": (
                    complete["Recovered prior close"].astype(float).mean() * 100 if not complete.empty else None
                ),
                "Median 20-session return": group["20-session return"].median(),
                "Median 30-session return": complete["30-session return"].median(),
                "Median maximum drawdown": complete["30-session maximum drawdown"].median(),
                "Median maximum favorable move": complete["30-session maximum favorable move"].median(),
                "Median sessions to recovery": complete.loc[
                    complete["Recovered prior close"].eq(True), "Sessions to recovery"
                ].median(),
                "Sample note": "Reviewable" if len(complete) >= 30 else "Small sample (<30 complete)",
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _feature_bucket_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Compare fixed, interpretable feature ranges in discovery and holdout data."""
    complete = frame[frame["30-session complete"]].copy()
    if complete.empty:
        return pd.DataFrame()
    complete["Excess-drop bucket"] = pd.cut(
        complete["Excess drop vs SPY"],
        bins=[float("-inf"), -10, -7.5, -5, float("inf")],
        labels=["≤ -10%", "-10% to -7.5%", "-7.5% to -5%", "> -5%"],
    )
    complete["Volatility bucket"] = pd.cut(
        complete["Drop / prior volatility"],
        bins=[float("-inf"), 2, 3, 5, float("inf")],
        labels=["< 2×", "2× to 3×", "3× to 5×", "≥ 5×"],
        right=False,
    )
    complete["Drop decomposition"] = complete.apply(
        lambda row: (
            "Overnight-led"
            if abs(row["Overnight gap"]) >= abs(row["Intraday return"])
            else "Intraday-led"
        ),
        axis=1,
    )
    summaries: list[pd.DataFrame] = []
    for label, bucket_column in (
        ("Excess drop vs SPY", "Excess-drop bucket"),
        ("Drop / prior volatility", "Volatility bucket"),
        ("Drop decomposition", "Drop decomposition"),
    ):
        eligible = complete.dropna(subset=[bucket_column])
        if eligible.empty:
            continue
        summary = _outcome_summary(eligible, ["Evaluation period", bucket_column])
        summary.insert(1, "Feature test", label)
        summary = summary.rename(columns={bucket_column: "Feature range"})
        summaries.append(summary)
    return pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()


OUTCOME_COLUMN_CONFIG = {
    "Recovery rate": st.column_config.NumberColumn(format="%.1f%%"),
    "Median 20-session return": st.column_config.NumberColumn(format="%.2f%%"),
    "Median 30-session return": st.column_config.NumberColumn(format="%.2f%%"),
    "Median maximum drawdown": st.column_config.NumberColumn(format="%.2f%%"),
    "Median maximum favorable move": st.column_config.NumberColumn(format="%.2f%%"),
    "Median sessions to recovery": st.column_config.NumberColumn(format="%.1f"),
}


def market_feature_lab_page(min_drop: float) -> None:
    """Compare deterministic market features across historical sector events."""
    st.title("Market feature test lab")
    st.caption(
        "Runs only deterministic Alpaca calculations—no SEC requests, news downloads, Ollama, Qwen, or score changes. "
        "Every qualifying event requires a following day-3 entry session. Forward horizons count that entry session as session 1."
    )
    try:
        constituents = load_watchlist(SP500)
    except Exception as exc:
        st.error(str(exc))
        return
    sectors = sorted({item.sector for item in constituents if item.sector})
    if not sectors:
        st.error("The S&P 500 file has no sector labels. Run `python -m scripts.refresh_sp500`, then reload this page.")
        return

    with st.form("market_feature_lab_controls"):
        lookback_days = st.select_slider(
            "Event lookback",
            options=[90, 180, 252, 365, 730],
            value=365,
            format_func=lambda value: f"{value} calendar days",
        )
        selected_sectors = st.multiselect("GICS sectors", sectors, default=sectors)
        sample_size = st.selectbox(
            "Constituents per sector",
            options=[5, 10, 25, "All"],
            index=1,
            help="A stable sample keeps Alpaca downloads small. Choose All for the complete selected sectors.",
        )
        submitted = st.form_submit_button("Run market feature test", type="primary")

    st.caption(
        f"Current event threshold: {min_drop:.1f}% close-to-close decline from the sidebar. "
        "Alpaca responses are cached for 15 minutes."
    )

    if submitted:
        if not selected_sectors:
            st.warning("Select at least one sector.")
        else:
            per_sector = None if sample_size == "All" else int(sample_size)
            selected = select_sector_sample(constituents, set(selected_sectors), per_sector)
            today = date.today()
            signal_start = today - timedelta(days=lookback_days)
            # The extra history supplies 20 pre-signal returns for events near
            # the beginning of the requested test window.
            fetch_start = signal_start - timedelta(days=60)
            symbols = tuple(dict.fromkeys([*(item.ticker for item in selected), "SPY"]))
            progress = st.progress(5, text=f"Loading {len(selected)} sampled constituents plus SPY…")
            try:
                bars_by_ticker = load_daily_bars(
                    symbols,
                    fetch_start,
                    today,
                    MARKET_FEATURE_LAB_VERSION,
                )
                progress.progress(70, text="Calculating every qualifying point-in-time event…")
                observations = build_market_feature_observations(
                    selected,
                    bars_by_ticker,
                    bars_by_ticker.get("SPY", []),
                    min_drop,
                    signal_start,
                )
                frame = pd.DataFrame(
                    {
                        "Ticker": item.ticker,
                        "Company": item.company,
                        "Sector": item.sector,
                        "Signal date": item.signal_day,
                        "Entry date": item.entry_day,
                        "Prior close": item.prior_close,
                        "Entry open": item.entry_open,
                        "Close-to-close drop": item.close_to_close_drop_pct,
                        "Excess drop vs SPY": item.excess_drop_vs_spy_pct,
                        "Drop / prior volatility": item.drop_volatility_multiple,
                        "Overnight gap": item.overnight_gap_pct,
                        "Intraday return": item.intraday_return_pct,
                        "5-session return": item.return_5_sessions_pct,
                        "10-session return": item.return_10_sessions_pct,
                        "20-session return": item.return_20_sessions_pct,
                        "30-session return": item.return_30_sessions_pct,
                        "Recovered prior close": item.recovered_prior_close_30_sessions,
                        "Sessions to recovery": item.sessions_to_recovery,
                        "30-session maximum drawdown": item.max_drawdown_30_sessions_pct,
                        "30-session maximum favorable move": item.max_favorable_30_sessions_pct,
                        "30-session complete": item.complete_30_sessions,
                    }
                    for item in observations
                )
                if not frame.empty:
                    frame = _assign_evaluation_period(frame)
                progress.progress(100, text="Market feature test complete.")
                progress.empty()
                st.session_state["market_feature_lab_results"] = frame
                st.session_state["market_feature_lab_scope"] = {
                    "version": MARKET_FEATURE_LAB_VERSION,
                    "symbols": len(selected),
                    "lookback_days": lookback_days,
                    "minimum_drop_pct": min_drop,
                }
            except Exception as exc:
                progress.empty()
                st.error(user_error(exc))

    frame = st.session_state.get("market_feature_lab_results")
    scope = st.session_state.get("market_feature_lab_scope")
    if not isinstance(frame, pd.DataFrame) or not isinstance(scope, dict):
        return
    if scope.get("version") != MARKET_FEATURE_LAB_VERSION:
        st.session_state.pop("market_feature_lab_results", None)
        st.session_state.pop("market_feature_lab_scope", None)
        st.info("Saved lab results use an older format. Run the market feature test again.")
        return
    if scope.get("minimum_drop_pct") != min_drop:
        st.info(
            f"Displayed results use a {scope.get('minimum_drop_pct'):.1f}% threshold. "
            f"Run the test again to apply the current {min_drop:.1f}% sidebar threshold."
        )
    if frame.empty:
        st.info("No qualifying events were found for the selected sectors, sample, threshold, and lookback.")
        return

    event_count = len(frame)
    ticker_count = frame["Ticker"].nunique()
    sector_count = frame["Sector"].nunique()
    complete_count = frame["30-session complete"].sum()
    metric_columns = st.columns(4)
    metric_columns[0].metric("Qualifying events", event_count)
    metric_columns[1].metric("Tickers with events", ticker_count)
    metric_columns[2].metric("Sectors represented", sector_count)
    metric_columns[3].metric("Complete 30-session outcomes", f"{complete_count}/{event_count}")

    st.subheader("Outcome comparison by sector")
    st.caption(
        "Recovery rate and 30-session statistics use complete 30-session windows only. "
        "The 20-session median includes every event with that horizon available."
    )
    sector_summary = _outcome_summary(frame, ["Sector"]).sort_values("Events", ascending=False)
    st.dataframe(sector_summary, hide_index=True, column_config=OUTCOME_COLUMN_CONFIG, width="stretch")

    st.subheader("Feature ranges: discovery vs holdout")
    st.caption(
        "Complete outcomes are split chronologically: the older 70% is for discovering patterns and the recent 30% "
        "is the holdout. Events sharing a signal date stay together, and fixed feature ranges avoid choosing cutoffs "
        "after seeing returns."
    )
    feature_summary = _feature_bucket_summary(frame)
    if feature_summary.empty:
        st.info("No complete 30-session outcomes are available for feature-range comparison yet.")
    else:
        st.dataframe(feature_summary, hide_index=True, column_config=OUTCOME_COLUMN_CONFIG, width="stretch")

    st.subheader("Event details")
    st.caption(
        f"{event_count} events from {scope.get('symbols', 0)} tested constituents over "
        f"{scope.get('lookback_days')} calendar days at a {scope.get('minimum_drop_pct'):.1f}% threshold."
    )
    st.dataframe(
        frame,
        hide_index=True,
        key="market_feature_lab_events",
        column_config={
            "Ticker": st.column_config.TextColumn(pinned=True),
            "Signal date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Entry date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Prior close": st.column_config.NumberColumn(format="$%.2f"),
            "Entry open": st.column_config.NumberColumn(format="$%.2f"),
            "Close-to-close drop": st.column_config.NumberColumn(format="%.2f%%"),
            "Excess drop vs SPY": st.column_config.NumberColumn(format="%.2f%%"),
            "Drop / prior volatility": st.column_config.NumberColumn(format="%.2f×"),
            "Overnight gap": st.column_config.NumberColumn(format="%.2f%%"),
            "Intraday return": st.column_config.NumberColumn(format="%.2f%%"),
            "5-session return": st.column_config.NumberColumn(format="%.2f%%"),
            "10-session return": st.column_config.NumberColumn(format="%.2f%%"),
            "20-session return": st.column_config.NumberColumn(format="%.2f%%"),
            "30-session return": st.column_config.NumberColumn(format="%.2f%%"),
            "Sessions to recovery": st.column_config.NumberColumn(format="%d"),
            "30-session maximum drawdown": st.column_config.NumberColumn(format="%.2f%%"),
            "30-session maximum favorable move": st.column_config.NumberColumn(format="%.2f%%"),
        },
        width="stretch",
    )
    st.download_button(
        "Download market feature CSV",
        frame.to_csv(index=False),
        "market_feature_events.csv",
        "text/csv",
    )


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


def display_market_data_preview(context: ResearchContext) -> None:
    """Show collected Alpaca bars without changing model input or scoring."""
    with st.container(border=True):
        st.subheader("Raw Alpaca market data")
        st.caption(
            "Inspection only: these bars are not sent to Qwen and do not affect evidence coverage or recovery scoring. "
            "Volume, trade count, and VWAP reflect the configured Alpaca feed."
        )
        bars = getattr(context, "market_bars", ())
        if not bars:
            st.info("No retained bars are available. Run fresh ticker research to populate this preview.")
            return
        frame = pd.DataFrame(
            {
                "Date": bar.day,
                "Open": bar.open,
                "High": bar.high,
                "Low": bar.low,
                "Close": bar.close,
                "Volume": bar.volume,
                "Trade count": bar.trade_count,
                "VWAP": bar.vwap,
            }
            for bar in reversed(bars)
        )
        st.caption(
            f"{len(frame)} most recent trading sessions retained · "
            f"volume present for {frame['Volume'].notna().sum()} · "
            f"trade count present for {frame['Trade count'].notna().sum()} · "
            f"VWAP present for {frame['VWAP'].notna().sum()}"
        )
        st.dataframe(
            frame,
            hide_index=True,
            column_config={
                "Date": st.column_config.DateColumn(format="YYYY-MM-DD", pinned=True),
                "Open": st.column_config.NumberColumn(format="$%.2f"),
                "High": st.column_config.NumberColumn(format="$%.2f"),
                "Low": st.column_config.NumberColumn(format="$%.2f"),
                "Close": st.column_config.NumberColumn(format="$%.2f"),
                "Volume": st.column_config.NumberColumn(format="%.0f"),
                "Trade count": st.column_config.NumberColumn(format="%d"),
                "VWAP": st.column_config.NumberColumn(format="$%.4f"),
            },
            width="stretch",
        )


def display_market_features_preview(context: ResearchContext) -> None:
    """Show deterministic drop-event features without changing model input."""
    with st.container(border=True):
        st.subheader("Market features preview")
        st.caption(
            "Inspection only: these features are not sent to Qwen and do not affect evidence coverage or recovery scoring. "
            "The event is the latest qualifying close-to-close decline with a following day-3 entry session."
        )
        features = getattr(context, "market_features", None)
        if features is None:
            st.info(
                "No actionable drop meeting the selected threshold was found in the fetched history. "
                "Run fresh ticker research after changing the minimum-drop control."
            )
            return

        st.caption(
            f"Reference close {features.prior_day} · signal close {features.signal_day} · "
            f"earliest entry {features.entry_day} · threshold {features.minimum_drop_pct:.1f}%"
        )
        feature_rows = [
            {
                "Feature": "Close-to-close drop",
                "Value": features.close_to_close_drop_pct,
                "Unit": "%",
                "What it shows": "The stock's day-1 close to day-2 close signal.",
            },
            {
                "Feature": "Excess drop vs SPY",
                "Value": features.excess_drop_vs_spy_pct,
                "Unit": "%",
                "What it shows": "Stock return minus SPY; more negative means more stock-specific weakness.",
            },
            {
                "Feature": "Drop / prior volatility",
                "Value": features.drop_volatility_multiple,
                "Unit": "×",
                "What it shows": "Absolute drop magnitude measured in normal daily-volatility units.",
            },
            {
                "Feature": "Overnight gap",
                "Value": features.overnight_gap_pct,
                "Unit": "%",
                "What it shows": "Day-1 close to day-2 open.",
            },
            {
                "Feature": "Signal-day intraday return",
                "Value": features.intraday_return_pct,
                "Unit": "%",
                "What it shows": "Day-2 open to day-2 close.",
            },
        ]
        st.dataframe(
            pd.DataFrame(feature_rows),
            hide_index=True,
            column_config={"Value": st.column_config.NumberColumn(format="%.2f")},
            width="stretch",
        )
        if features.prior_daily_volatility_pct is None:
            st.caption(
                f"Volatility unavailable: {features.volatility_return_count} of 20 required pre-signal returns were available."
            )
        st.caption("The overnight and intraday returns compound to the close-to-close result; they are not simply additive.")


def _format_days_until(days: int | None) -> str:
    if days is None:
        return "Unavailable"
    if days < 0:
        return f"Overdue by {abs(days)} calendar days"
    return f"{days} calendar days"


def ticker_research_section(minimum_drop_pct: float) -> None:
    refresh_stale_research_session()
    st.header("Ticker research")
    st.caption("Combines recent market data, SEC evidence, and bounded readable news excerpts, then asks the local Qwen3 model for a structured, evidence-grounded assessment.")
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
                context = research_service().collect(
                    ticker,
                    minimum_drop_pct=minimum_drop_pct,
                    on_stage=progress.begin,
                )
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
    context = st.session_state.get("ticker_research_context")
    if isinstance(report, ResearchReport) and hasattr(report, "evidence_coverage"):
        display_report(report)
        if isinstance(context, ResearchContext):
            display_market_features_preview(context)
            display_market_data_preview(context)
            display_qwen_evidence_preview(context)
            display_earnings_preview(context.earnings)
            st.write("**Sources**")
            if context.news:
                for article in context.news:
                    published = article.published_at.date().isoformat() if article.published_at else "date unavailable"
                    reading_status = "excerpt read by Qwen" if article.excerpt else "headline only"
                    st.markdown(f"- [{article.title}]({article.url}) · {article.publisher} · {published} · {reading_status}")
            else:
                st.caption("No recent news articles were returned.")
        st.caption("Research output is informational only and is not investment advice.")
    elif report is not None:
        st.session_state.pop("ticker_research_report", None)
        st.info("The saved report used the prior one-score format. Run ticker research again to calculate recovery score and evidence coverage.")
    elif isinstance(context, ResearchContext):
        display_market_features_preview(context)
        display_market_data_preview(context)


def research_and_screen_page(min_drop: float) -> None:
    ticker_research_section(min_drop)
    st.divider()
    screen_page(min_drop)


def main() -> None:
    min_drop = configure_sidebar()
    page = st.navigation(
        [
            st.Page(
                lambda: research_and_screen_page(min_drop),
                title="Research and screener",
                icon="📉",
                url_path="screener",
                default=True,
            ),
            st.Page(
                lambda: market_feature_lab_page(min_drop),
                title="Market feature lab",
                icon="🧪",
                url_path="market-feature-lab",
            ),
        ]
    )
    page.run()
