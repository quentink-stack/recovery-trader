# Recovery Trader

A local research screener and strategy lab for sharp single-day equity declines.


## Local Ollama

Ticker research uses Ollama at `http://localhost:11434` with the `qwen3:8b-q4_K_M` model by default. Install Ollama, then run these commands in separate PowerShell windows:

```powershell
ollama serve
```

## Run the web app

```powershell
ollama pull qwen3:8b-q4_K_M
cd 'C:\Users\Quentin\Documents\Personal Projects\git\recovery-trader'
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

The app opens in your browser. It is a local, read-only research dashboard and contains no order endpoints.

Leave `ollama serve` running. Ollama loads the model automatically on the first ticker-research request. To load it before using the app, run:

```powershell
ollama run qwen3:8b-q4_K_M "Reply READY"
```

If `ollama serve` reports that the address is already in use, Ollama is already running and you can continue with the second window.

The client reads these settings automatically when it starts. The values shown below are the defaults; set any of them before starting Streamlit to override them:

```powershell
$env:OLLAMA_BASE_URL = 'http://localhost:11434'
$env:OLLAMA_MODEL = 'qwen3:8b-q4_K_M'
$env:OLLAMA_TIMEOUT = 420
$env:OLLAMA_TEMPERATURE = 0.15
```

`OLLAMA_TIMEOUT` is measured in seconds and defaults to 420 (seven minutes). A temperature of `0.15` favors repeatable, evidence-grounded structured reports while preserving Qwen's reasoning mode. Restart Streamlit after changing any Ollama environment variable.

The reusable client is in `ollama_client.py`. It provides `is_available()` for a health check and `generate()` for non-streaming model responses. JSON mode is enabled by default for the structured research report planned below.

The first research data source is the public Google News RSS search feed. `recovery_trader/integrations/news.py` returns normalized article titles, publishers, links, and publication timestamps without requiring another API key.

`ResearchService.collect()` combines those articles with recent Alpaca daily bars into a normalized, JSON-serializable `ResearchContext`. This context is the input boundary for the next step: building the structured Ollama prompt and validated research report.

`recovery_trader/research/report.py` builds that prompt and parses the Qwen response into a `ResearchReport`. The report requires market, earnings, news, macro, regulation, and sentiment assessments. Python calculates two separate 0–100 values: a weighted recovery score from those ratings and deterministic evidence coverage from the market/news inputs actually supplied to the model.

The Streamlit app now includes a **Ticker research** section. Enter a symbol and select **Research ticker** to collect recent Alpaca prices and Google News articles, generate a Qwen3 report, and view the recovery score, evidence coverage, category evidence, catalysts, risks, uncertainties, and source links.

## S&P 500 screener

The drop screener defaults to the locally stored S&P 500 universe in `data/sp500.csv`. It fetches daily bars in batches of 100 symbols and caches the result for 15 minutes, keeping a typical 60–120-day full-index scan to about five Alpaca historical-data requests rather than roughly 500 individual requests.

Refresh the local constituent list deliberately when needed:

```powershell
python refresh_sp500.py
```

The refresh script reads the constituent table from Wikipedia and writes the resulting symbol and company list into `data/sp500.csv`. Review the generated change before committing it; index membership changes over time.

## Alpaca Basic research mode

Create a free Alpaca paper account and paste its API key and secret into `config/alpaca.ini`. This file is ignored by Git.

```ini
[alpaca]
api_key = paste your Alpaca paper key here
api_secret = paste your Alpaca paper secret here
equities_feed = iex
```

## SEC EDGAR setup

The SEC integration uses public filing data and requires a descriptive User-Agent containing a real contact email. Create `config/sec_edgar.ini` from `config/sec_edgar.example.ini`; it is ignored by Git.

```ini
[sec_edgar]
user_agent = Recovery Trader your-email@example.com
timeout_seconds = 30
```

Alternatively, set `SEC_USER_AGENT` before starting Python. `SEC_TIMEOUT` can override the 30-second request timeout.

When ticker research runs, Recovery Trader fetches SEC filing metadata and 10-Q/10-K facts for preview in the app. The preview also shows the public-release age, a freshness-adjusted earnings-confidence indicator, and an estimated next earnings date based on recent Item 2.02 filing cadence. The estimate is not company-confirmed. These preview measures do not modify raw GAAP values and are not yet supplied to Qwen or used in either top-level score.

The Streamlit app reads Alpaca Basic market data only; it has no order, account, or position endpoints.

- **Run dip-recovery backtest** downloads two years of daily bars for a ticker and tests a simple proxy: a qualifying drop from one session's close to the next session's close, entry at the following open, a 15-trading-day maximum hold, 10% stop, and 12% target.

The backtest measures underlying-price returns and avoids using the signal day's closing price as an entry. Alpaca Basic's IEX data is suitable for development and hypothesis testing—not execution-quality pricing.
