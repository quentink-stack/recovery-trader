"""Browser UI for Recovery Trader's read-only Alpaca research workflow."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd
import streamlit as st

from alpaca import AlpacaMarketData
from backtest import STRATEGIES, run_strategy
from screener import latest_large_drop, load_watchlist

ROOT = Path(__file__).parent
WATCHLIST = ROOT / "data" / "watchlist.csv"
SP500 = ROOT / "data" / "sp500.csv"

st.set_page_config(page_title="Recovery Trader", page_icon="📉", layout="wide")


@st.cache_resource
def client() -> AlpacaMarketData:
    return AlpacaMarketData.from_config()


@st.cache_data(ttl="15m", max_entries=16, show_spinner=False)
def load_daily_bars(tickers: tuple[str, ...], start: date, end: date) -> dict[str, list]:
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
        min_drop = st.slider("Minimum one-day drop", min_value=2.0, max_value=20.0, value=5.0, step=0.5, format="%.1f%%")
        st.divider()
        st.caption("Data mode: Alpaca Basic — IEX equities. Read-only; no order endpoints are used.")
    return min_drop


def screen_page(min_drop: float) -> None:
    st.title("Large single-day drop screener")
    st.caption("Finds the most recent qualifying close-to-close decline in the selected local ticker universe.")
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
            bars_by_ticker = load_daily_bars(tuple(item.ticker for item in watchlist), date.today() - timedelta(days=days), date.today())
            progress.progress(75, text="Screening for qualifying drops…")
            for item in watchlist:
                bars = bars_by_ticker.get(item.ticker, [])
                result = latest_large_drop(item, bars, min_drop)
                if result:
                    research.append(result)
            progress.empty()
            st.session_state["screen_results"] = research
            st.session_state["screen_scope"] = universe_name
        except Exception as exc:
            st.error(user_error(exc))
    results = st.session_state.get("screen_results", [])
    if results:
        frame = pd.DataFrame([{
            "Ticker": item.ticker, "Company": item.company, "Drop date": item.signal_day,
            "One-day drop": item.drop_pct / 100, "Signal close": item.signal_close,
            "Latest close": item.latest_close, "Since-signal return": item.recovery_pct / 100,
        } for item in results]).sort_values("One-day drop")
        st.caption(f"{st.session_state.get('screen_scope', 'Selected universe')}: {len(results)} qualifying symbols. Daily bars are cached for 15 minutes.")
        st.dataframe(frame, hide_index=True, column_config={
            "One-day drop": st.column_config.NumberColumn(format="%.2f%%"),
            "Since-signal return": st.column_config.NumberColumn(format="%.2f%%"),
            "Signal close": st.column_config.NumberColumn(format="$%.2f"),
            "Latest close": st.column_config.NumberColumn(format="$%.2f"),
        })
        st.download_button("Download screener CSV", frame.to_csv(index=False), "drop_screener.csv", "text/csv")
    elif "screen_results" in st.session_state:
        st.info("No current watchlist ticker had a qualifying drop in that lookback window.")


def backtest_page(min_drop: float) -> None:
    st.title("Recovery-strategy lab")
    st.caption("No-look-ahead underlying-price proxy. This does not estimate option P&L, spreads, IV, or theta decay.")
    col1, col2 = st.columns(2)
    with col1:
        years = st.selectbox("History", options=[1, 2, 3, 5], index=1, format_func=lambda value: f"{value} year{'s' if value > 1 else ''}")
    with col2:
        mode = st.radio("Run", ["Single ticker", "Watchlist comparison"], horizontal=True)
    ticker = st.text_input("Ticker", value="PGR", disabled=mode != "Single ticker").strip().upper()
    if st.button("Run backtest", type="primary"):
        try:
            market_data = client()
            start = date.today() - timedelta(days=365 * years)
            if mode == "Single ticker":
                bars = market_data.daily_bars(ticker, start, date.today())
                results = {strategy.name: run_strategy(bars, min_drop, strategy).trades for strategy in STRATEGIES}
                st.session_state["backtest_scope"] = ticker
            else:
                watchlist = load_watchlist(WATCHLIST)
                results = {strategy.name: [] for strategy in STRATEGIES}
                progress = st.progress(0, text="Loading watchlist history…")
                for index, item in enumerate(watchlist, start=1):
                    bars = market_data.daily_bars(item.ticker, start, date.today())
                    for strategy in STRATEGIES:
                        results[strategy.name].extend(run_strategy(bars, min_drop, strategy).trades)
                    progress.progress(index / len(watchlist), text=f"Backtesting {item.ticker} ({index}/{len(watchlist)})")
                progress.empty()
                st.session_state["backtest_scope"] = f"{len(watchlist)}-ticker watchlist"
            st.session_state["backtest_results"] = results
        except Exception as exc:
            st.error(user_error(exc))
    results = st.session_state.get("backtest_results")
    if results is not None:
        summary = []
        for strategy in STRATEGIES:
            trades = results[strategy.name]
            summary.append({"Strategy": strategy.name, "Trades": len(trades), "Win rate": (sum(t.return_pct > 0 for t in trades) / len(trades) if trades else 0), "Average return": (sum(t.return_pct for t in trades) / len(trades) / 100 if trades else 0), "Rules": strategy.description})
        summary_frame = pd.DataFrame(summary)
        st.subheader(f"{st.session_state.get('backtest_scope', 'Results')} results")
        st.dataframe(summary_frame, hide_index=True, column_config={"Win rate": st.column_config.NumberColumn(format="%.1f%%"), "Average return": st.column_config.NumberColumn(format="%.2f%%")})
        selected_strategy = st.selectbox("Inspect trades", [strategy.name for strategy in STRATEGIES])
        trades = results[selected_strategy]
        if trades:
            frame = pd.DataFrame([{"Entry": t.entry_day, "Exit": t.exit_day, "Entry price": t.entry_price, "Exit price": t.exit_price, "Return": t.return_pct / 100, "Exit reason": t.exit_reason} for t in trades])
            st.dataframe(frame, hide_index=True, column_config={"Entry price": st.column_config.NumberColumn(format="$%.2f"), "Exit price": st.column_config.NumberColumn(format="$%.2f"), "Return": st.column_config.NumberColumn(format="%.2f%%")})
        else:
            st.info("This strategy generated no trades in the selected history.")


def main() -> None:
    min_drop = configure_sidebar()
    page = st.navigation([
        st.Page(lambda: screen_page(min_drop), title="Drop screener", icon="📉", url_path="screener", default=True),
        st.Page(lambda: backtest_page(min_drop), title="Strategy lab", icon="🧪", url_path="strategies"),
    ])
    page.run()


if __name__ == "__main__":
    main()
