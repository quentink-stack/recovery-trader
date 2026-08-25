"""Streamlit presentation layer for the Recovery Trader screener."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd
import streamlit as st

from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.integrations.news import NewsClient
from recovery_trader.integrations.ollama import OllamaClient
from recovery_trader.domain.screener import latest_large_drop, load_watchlist
from recovery_trader.research.report import ResearchReport, generate_report
from recovery_trader.research.service import ResearchService

ROOT = Path(__file__).parents[2]
SP500 = ROOT / "data" / "sp500.csv"
SCREEN_DATA_VERSION = "day-two-signal-v1"

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
def research_service() -> ResearchService:
    return ResearchService(client(), news_client())


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


def display_report(report: ResearchReport) -> None:
    st.subheader(f"{report.ticker} research report")
    score_column, summary_column = st.columns([1, 3])
    with score_column:
        st.metric("Research confidence", f"{report.score}/100")
    with summary_column:
        st.write(report.summary)

    st.write("**Category assessments**")
    assessment_frame = pd.DataFrame([
        {"Category": category.title(), "Rating": assessment.rating.title(), "Evidence": assessment.evidence}
        for category, assessment in report.assessments.items()
    ])
    st.dataframe(assessment_frame, hide_index=True)

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


def ticker_research_section() -> None:
    st.header("Ticker research")
    st.caption("Combines recent market data and news, then asks the local Qwen3 model for a structured, evidence-grounded assessment.")
    ticker = st.text_input("Ticker to research", placeholder="e.g. AAPL").strip().upper()
    if st.button("Research ticker", type="primary"):
        if not ticker:
            st.warning("Enter a ticker symbol first.")
        else:
            try:
                with st.spinner(f"Collecting evidence and analyzing {ticker}…"):
                    context = research_service().collect(ticker)
                    report = generate_report(context, ollama_client())
                st.session_state["ticker_research_context"] = context
                st.session_state["ticker_research_report"] = report
            except Exception as exc:
                st.error(user_error(exc))

    report = st.session_state.get("ticker_research_report")
    if isinstance(report, ResearchReport):
        display_report(report)
        context = st.session_state.get("ticker_research_context")
        if context is not None:
            st.write("**Sources**")
            if context.news:
                for article in context.news:
                    published = article.published_at.date().isoformat() if article.published_at else "date unavailable"
                    st.markdown(f"- [{article.title}]({article.url}) · {article.publisher} · {published}")
            else:
                st.caption("No recent news articles were returned.")
        st.caption("Research output is informational only and is not investment advice.")


def main() -> None:
    min_drop = configure_sidebar()
    ticker_research_section()
    st.divider()
    page = st.navigation([st.Page(lambda: screen_page(min_drop), title="Drop screener", icon="📉", url_path="screener", default=True)])
    page.run()
