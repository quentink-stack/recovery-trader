# Recovery Trader

A local research screener for sharp single-day equity declines.

## Pull request reviews

CodeRabbit is configured in [`.coderabbit.yaml`](.coderabbit.yaml) as an additional
PR reviewer. It reviews non-draft PRs targeting the default branch automatically,
with guidance covering research continuity, data handling, and scoring correctness.

To activate it, a repository administrator must
[install the CodeRabbit GitHub app](https://github.com/apps/coderabbitai) for
`quentink-stack/recovery-trader`, then merge this configuration into the default
branch. Configuration alone does not install or activate the service.
Review availability depends on the account's CodeRabbit plan.

For an existing PR, comment `@coderabbitai review` to request a review.
See the [automatic review documentation](https://docs.coderabbit.ai/configuration/auto-review)
for trigger details.

## Saved research and results

Start at [research/README.md](research/README.md) for the current findings, dated
experiment records, preserved aggregate CSVs, reproduction steps, and pending
research. These files are intended for Git; raw exports and credentials remain
ignored. The research index explains how to save future experiments and back up
the raw data separately. Root `AGENTS.md` directs future coding sessions to read
this record before changing research logic.


## Local Ollama

Ticker research uses Ollama at `http://localhost:11434` with the `qwen3:14b` model by default. Install Ollama, then run these commands in separate PowerShell windows:

```powershell
ollama serve
```

## Run the web app

```powershell
ollama pull qwen3:14b
cd 'C:\Users\Quentin\Documents\Personal Projects\git\recovery-trader'
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

The app opens in your browser. It is a local, read-only research dashboard and contains no order endpoints.

Leave `ollama serve` running. Ollama loads the model automatically on the first ticker-research request. To load it before using the app, run:

```powershell
ollama run qwen3:14b "Reply READY"
```

If `ollama serve` reports that the address is already in use, Ollama is already running and you can continue with the second window.

The client reads these settings automatically when it starts. The values shown below are the defaults; set any of them before starting Streamlit to override them:

```powershell
$env:OLLAMA_BASE_URL = 'http://localhost:11434'
$env:OLLAMA_MODEL = 'qwen3:14b'
$env:OLLAMA_TIMEOUT = 420
$env:OLLAMA_TEMPERATURE = 0.15
```

`OLLAMA_TIMEOUT` is measured in seconds and defaults to 420 (seven minutes). A temperature of `0.15` favors repeatable, evidence-grounded structured reports while preserving Qwen's reasoning mode. Restart Streamlit after changing any Ollama environment variable.

The reusable client is in `recovery_trader/integrations/ollama.py`. It provides `is_available()` for a health check and `generate()` for non-streaming model responses. JSON mode is enabled by default for the structured research report planned below.

The first research data source is the public Google News RSS search feed. For each ticker-research request, Recovery Trader considers articles in feed order, resolves Google News wrappers to their publisher URLs, and continues through the candidates until it extracts three readable, bounded excerpts or exhausts the returned articles. The RSS feed fetch has a 30-second default timeout; publisher-resolution and article-processing fetches each have a 12-second timeout, and article downloads have a 1 MB limit. Inaccessible, paywalled, non-HTML, blocked, or unresolved pages remain headline-only without failing research. The Qwen prompt includes the excerpt when available, alongside the title, publisher, and publication timestamp; resolved source links remain clickable in the app but are not sent to Qwen. No additional API key is required.

`ResearchService.collect()` combines those articles with recent Alpaca daily bars into a normalized, JSON-serializable `ResearchContext`. This context is the input boundary for the next step: building the structured Ollama prompt and validated research report.

`recovery_trader/research/report.py` builds that prompt and parses the Qwen response into a `ResearchReport`. The report requires market, earnings, news, macro, regulation, and sentiment assessments. Python calculates two separate 0–100 values: an evidence-adjusted recovery score from those ratings and deterministic evidence coverage from the market, news, and compact SEC inputs actually supplied to the model.

The Streamlit app now includes a **Ticker research** section. Enter a symbol and select **Research ticker** to collect recent Alpaca prices and Google News articles, generate a Qwen3 report, and view the recovery score, evidence coverage, category evidence, catalysts, risks, uncertainties, and source links.

## S&P 500 screener

The drop screener defaults to the locally stored S&P 500 universe in `data/sp500.csv`. It fetches daily bars in batches of 100 symbols and caches the result for 15 minutes, keeping a typical 60–120-day full-index scan to about five Alpaca historical-data requests rather than roughly 500 individual requests.

Refresh the local constituent list deliberately when needed:

```powershell
python -m scripts.refresh_sp500
```

The refresh script reads the constituent table from Wikipedia and writes each symbol, company, and GICS sector into `data/sp500.csv`. Review the generated change before committing it; index membership and sector classifications change over time.

## Market feature test lab

To export the analysis without opening Streamlit, run:

```powershell
python -m scripts.export_market_analysis --days 730 --min-drop 5
```

This uses all constituents in the saved universe and your existing `config/alpaca.ini` credentials.
For a smaller run, add `--per-sector 10` or `--tickers CSCO,JPM,XOM`.
Each run creates a new ignored `exports/market-analysis-.../` directory with `events.csv`,
`daily_bars.csv`, `universe_coverage.csv`, and `metadata.json`. Share `events.csv` and
`metadata.json` for analysis; raw bars allow independent calculation checks. The export
excludes the current day and contains no API credentials. Use `--as-of YYYY-MM-DD`
for a historical exclusive cutoff. The chronological split reproduces the lab but does
not purge overlapping forward windows, so treat comparisons as exploratory.

The **Market feature lab** page tests deterministic price features without calling SEC, news, Ollama, or Qwen. Choose a historical window, GICS sectors, and a bounded per-sector sample. The lab finds every qualifying close-to-close drop with a following day-3 entry session and compares five values: the raw drop, excess drop versus SPY, drop magnitude versus prior 20-session volatility, overnight gap, and signal-day intraday return.

Historical outcomes use the day-3 open as entry and report 5-, 10-, 20-, and 30-trading-session returns, counting the entry session as session 1. The lab also measures close-based recovery to the pre-drop price, sessions to recovery, and complete-window maximum drawdown and favorable movement. Unknown future windows remain incomplete rather than being counted as failures. Sector summaries and fixed feature ranges exclude missing horizons, while complete outcomes are split chronologically into an older 70% discovery sample and a recent 30% holdout sample.

Results include sector comparisons, feature-range comparisons, individual event rows, and CSV export. An extra 60 calendar days are fetched before the selected window so early events can calculate their pre-signal volatility without lookahead. Alpaca results use the same 15-minute cache as the screener.

### Offline return-consistency analysis

Analyze an existing export without new Alpaca requests or Qwen calls:

```powershell
python -m scripts.analyze_market_consistency exports/market-analysis-20260909-152502-884305
```

Replace the directory with your export. A new ignored `exports/consistency-.../`
directory contains `summary.csv`, `by_quarter.csv`, `by_sector.csv`, individual
`trades.csv`, and methodology/source hashes in `metadata.json`. `--output` may
specify a different **new** directory; existing results are never overwritten.

This experiment compares five fixed exits: 20-session hold, 30-session hold,
close-based recovery to the pre-drop close (30-session cap), that recovery exit
with a 10% close-based stop, and a 10% closing-price trailing stop (30-session cap).
Recovery/stop conditions are observed at the close and filled at the **next open**,
including gaps; these are not intraday stops or guaranteed loss limits. A trailing
stop uses the highest observed close or entry price, not intraday highs. Entry is
still day 3's open and counts as session 1. This does not change the app's other
strategy implementations or Qwen scoring.

Every exit uses the same complete 30-session sample. The earliest eligible entry
per ticker blocks further entries for its entire 30-session window, even if an
exit occurs sooner. Older windows reaching the recent-period boundary are purged
first. The second entry-filter comparison retains events with a drop between 2
and 5 times prior daily volatility (2 inclusive, 5 exclusive), applied after this
common cohort selection. No threshold optimization is performed.

Reports include median **and mean** returns, win rates, 10th-percentile losses,
worst losses, matched-date/open-or-close SPY excess returns, date-balanced medians,
and sensitivity to excluding the three largest signal-date clusters. Costs are
tested at 0, 10, and 25 basis points **per side**, applied to stock and benchmark.
Quarter/sector breadth counts only groups with at least 30 trades; quarters may
be partial. `adverse_excursion_pct` is the worst exposed price versus entry, not
peak-to-trough portfolio drawdown.

The older/recent split is a robustness check, **not an untouched holdout** once
you have reviewed this dataset. Cross-ticker correlation, current-constituent
survivorship bias, and IEX pricing limitations remain. These are per-trade stock
results, not a capital-constrained portfolio, earnings-only study, or options
backtest. Higher win rates or medians can come at the expense of mean returns;
require new, unseen data before treating an apparent improvement as established.

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

When ticker research runs, Recovery Trader fetches SEC filing metadata and 10-Q/10-K facts for preview in the app. The preview includes prior-year same-period values plus operating income, operating cash flow, capex, debt, and diluted shares. A deterministic brief checks fiscal-period alignment and skips cash-flow/capex/debt direction rules for financial-sector SIC codes. It also shows the public-release age, a freshness-adjusted earnings-confidence indicator, and an estimated next earnings date based on recent Item 2.02 filing cadence. The estimate is not company-confirmed.

Regular ticker research sends the compact deterministic SEC brief to Qwen as JSON. Python calculates earnings evidence confidence from 40% current-data coverage and 60% comparable-period coverage, then multiplies it by event freshness. That confidence becomes earnings evidence coverage and attenuates the 25%-weighted earnings rating toward neutral; freshness never changes the raw GAAP values or makes the direction positive or negative by itself.

The Streamlit app reads Alpaca Basic market data only; it has no order, account, or position endpoints.

The S&P 500 screener identifies a qualifying decline from day 1's close to day 2's close. It then shows day 3's opening price as the earliest modeled entry, plus the first closing prices on or after one week and 30 calendar days after the signal. The 30-day percentage change is measured from the day-2 signal close to that 30-day closing-price checkpoint. These are research observations, not simulated trades or exit results.

The currently exposed Streamlit interface does not include a backtest control. Alpaca Basic's IEX data is suitable for development and hypothesis testing—not execution-quality pricing.
