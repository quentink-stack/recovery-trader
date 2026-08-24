"""Streamlit presentation layer for the Recovery Trader screener."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd
import streamlit as st

from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.domain.screener import latest_large_drop, load_watchlist

ROOT = Path(__file__).parents[2]
WATCHLIST = ROOT / "data" / "watchlist.csv"
SP500 = ROOT / "data" / "sp500.csv"
SCREEN_DATA_VERSION = "adjusted-bars-v1"

st.set_page_config(page_title="Recovery Trader", page_icon="📉", layout="wide")


@st.cache_resource
def client() -> AlpacaMarketData:
    return AlpacaMarketData.from_config()


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
    st.caption("Finds the most recent qualifying decline from one session's close to the next session's close, then shows closing prices one week and 30 days later.")
    universe_name = st.segmented_control("Universe", ["My watchlist", "S&P 500"], default="S&P 500", selection_mode="single")
    days = st.select_slider("Lookback", options=[60, 90, 120, 180, 252], value=120, format_func=lambda value: f"{value} calendar days")
    universe_path = SP500 if universe_name == "S&P 500" else WATCHLIST
    button_label = "Screen S&P 500" if universe_name == "S&P 500" else "Screen watchlist"
    if st.button(button_label, type="primary"):
        try:
            watchlist = load_watchlist(universe_path)
            if not watchlist:
                raise ValueError(f"{universe_path.name} does not contain any tickers.")
            research = []
            progress = st.progress(10, text=f"Loading {len(watchlist)} symbols in Alpaca batches…")
            bars_by_ticker = load_daily_bars(tuple(item.ticker for item in watchlist), date.today() - timedelta(days=days), date.today(), SCREEN_DATA_VERSION)
            progress.progress(75, text="Screening for qualifying drops…")
            for item in watchlist:
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
    results_are_current = st.session_state.get("screen_data_version") == SCREEN_DATA_VERSION and all(hasattr(item, "one_week_close") and hasattr(item, "thirty_day_close") for item in results)
    if results and not results_are_current:
        st.session_state.pop("screen_results", None)
        st.session_state.pop("screen_scope", None)
        st.session_state.pop("screen_data_version", None)
        results = []
        st.info("Saved results used a prior market-data format. Run a fresh screen to use corporate-action-adjusted prices.")
    if results:
        frame = pd.DataFrame([{
            "Ticker": item.ticker, "Company": item.company, "Drop date": item.signal_day,
            "One-day drop": item.drop_pct, "Signal close": item.signal_close,
            "1-week close": item.one_week_close, "30-day close": item.thirty_day_close,
            "30 day % change": item.thirty_day_pct_change,
        } for item in results]).sort_values("One-day drop")
        st.caption(f"{st.session_state.get('screen_scope', 'Selected universe')}: {len(results)} qualifying symbols. Daily bars are cached for 15 minutes.")
        styled_frame = frame.style.apply(lambda column: ["color: green; font-weight: 600" if pd.notna(value) and value > 0 else "color: red; font-weight: 600" if pd.notna(value) and value < 0 else "" for value in column], subset=["30 day % change"])
        st.dataframe(styled_frame, hide_index=True, column_config={"One-day drop": st.column_config.NumberColumn(format="%.2f%%"), "Signal close": st.column_config.NumberColumn(format="$%.2f"), "1-week close": st.column_config.NumberColumn(format="$%.2f"), "30-day close": st.column_config.NumberColumn(format="$%.2f"), "30 day % change": st.column_config.NumberColumn(format="%.2f%%")})
        st.download_button("Download screener CSV", frame.to_csv(index=False), "drop_screener.csv", "text/csv")
    elif "screen_results" in st.session_state:
        st.info("No current watchlist ticker had a qualifying drop in that lookback window.")


def main() -> None:
    min_drop = configure_sidebar()
    page = st.navigation([st.Page(lambda: screen_page(min_drop), title="Drop screener", icon="📉", url_path="screener", default=True)])
    page.run()
