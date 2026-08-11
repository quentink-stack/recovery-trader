# Recovery Trader

A local research screener and strategy lab for sharp single-day equity declines.

## Run the web app

```powershell
cd 'C:\Users\Quentin\Documents\Personal Projects\git\recovery-trader'
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

The app opens in your browser. It is a local, read-only research dashboard and contains no order endpoints.

## S&P 500 screener

The drop screener defaults to the locally stored S&P 500 universe in `data/sp500.csv`. It fetches daily bars in batches of 100 symbols and caches the result for 15 minutes, keeping a typical 60–120-day full-index scan to about five Alpaca historical-data requests rather than roughly 500 individual requests.

Refresh the local constituent list deliberately when needed:

```powershell
python refresh_sp500.py
```

The refresh script reads the constituent table from Wikipedia and writes the resulting symbol and company list into `data/sp500.csv`. Review the generated change before committing it; index membership changes over time.

## Alpaca Basic research mode

Create a free Alpaca paper account and paste its API key and secret into `alpaca.ini`. This file is ignored by Git.

```ini
[alpaca]
api_key = paste your Alpaca paper key here
api_secret = paste your Alpaca paper secret here
equities_feed = iex
```

The Streamlit app reads Alpaca Basic market data only; it has no order, account, or position endpoints.

- **Run dip-recovery backtest** downloads two years of daily bars for a ticker and tests a simple proxy: a qualifying close-to-close dip, entry at the next open, a 15-trading-day maximum hold, 10% stop, and 12% target.

The backtest measures underlying-price returns and avoids using the signal day's closing price as an entry. Alpaca Basic's IEX data is suitable for development and hypothesis testing—not execution-quality pricing.

## Watchlist research and strategy comparison

Edit `data/watchlist.csv` to define the tickers you want to research. The application includes three watchlist tools:

- **Screen watchlist drops** finds each ticker's most recent qualifying close-to-close decline within a 120-calendar-day lookback and shows its subsequent return to the latest available close.
- **Compare strategies** runs all strategies across the complete watchlist and the preceding two years of daily bars.
- **Run dip-recovery backtest** runs the current bracket strategy against one ticker and shows every proxy trade.

The included strategies include fixed 15-session holds, bracket exits, rebound or breakout confirmation rules, and a 10% trailing-stop exit. The trailing stop begins at the entry price and updates only after each completed daily bar, so it does not assume an intraday high occurred before an intraday low. Results are pooled across all tickers for comparison. They do not model spreads, commissions, assignment, implied volatility, option decay, or earnings-specific causality.
