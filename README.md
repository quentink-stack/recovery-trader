# Recovery Trader

A local research screener and strategy lab for sharp single-day equity declines.

## Run the web app

```powershell
cd 'C:\Users\Quentin\Documents\Personal Projects\git\recovery-trader'
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

The app opens in your browser. It is a local, read-only research dashboard and contains no order endpoints.

## Desktop app

The original Tkinter desktop version remains available:

```powershell
python app.py
```

## CSV format

Use the header in `data/sample_candidates.csv`:

```text
ticker,company,drop_pct,low_hold_pct,iv_percentile,call_open_interest,dte,risk_note
```

`low_hold_pct` is the percentage of the earnings-day low retained after the event (a higher value indicates the post-event low has held better). The rebound score is a research-priority heuristic, not a trading signal.

## Scoring

The 0–100 score combines the magnitude of the selloff (25 points), low hold (30), IV percentile (20), call open interest (15), and closeness to 38 DTE (10). The screen only includes candidates that pass every configured filter.

Market-data collection is deliberately separate from the scoring engine. Bring data in through CSV first; this keeps the research logic testable and avoids presenting stale sample values as live prices or tradable options.

## Alpaca Basic research mode

Create a free Alpaca paper account and paste its API key and secret into `alpaca.ini`. This file is ignored by Git.

```ini
[alpaca]
api_key = paste your Alpaca paper key here
api_secret = paste your Alpaca paper secret here
equities_feed = iex
```

In the desktop app, select **Connect Alpaca Basic**. The integration is read-only and has no order, account, or position endpoints.

- **Run dip-recovery backtest** downloads two years of daily bars for a ticker and tests a simple proxy: a qualifying close-to-close dip, entry at the next open, a 15-trading-day maximum hold, 10% stop, and 12% target.

The backtest measures underlying-price returns and avoids using the signal day's closing price as an entry. Alpaca Basic's IEX data is suitable for development and hypothesis testing—not execution-quality pricing.

## Watchlist research and strategy comparison

Edit `data/watchlist.csv` to define the tickers you want to research. The application includes three watchlist tools:

- **Screen watchlist drops** finds each ticker's most recent qualifying close-to-close decline within a 120-calendar-day lookback and shows its subsequent return to the latest available close.
- **Compare strategies** runs all strategies across the complete watchlist and the preceding two years of daily bars.
- **Run dip-recovery backtest** runs the current bracket strategy against one ticker and shows every proxy trade.

The included strategies are: next-open 15-session hold; 10% stop / 12% target bracket; and a next-day confirmation rule followed by a 15-session hold. Results are pooled across all tickers for comparison. They do not model spreads, commissions, assignment, implied volatility, option decay, or earnings-specific causality.
