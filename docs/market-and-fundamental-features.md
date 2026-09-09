# Market and fundamental features for recovery forecasting

Date: September 3, 2026

## Recommendation

Build the next analysis layer around the *cause and shape of the drop*, not
around a single technical indicator. RSI and VWAP are useful, but they should
be low-weight confirmations. Market/sector-relative shock, early stabilization,
volatility, and volume/liquidity have a more direct connection to the recovery
hypothesis. SEC-derived financial strength should be a separate quality layer.

The key research distinction is this: a liquidity-driven or sentiment-driven
selloff may reverse, while a price change that incorporates durable fundamental
news may continue. Research by Da, Liu, and Schaumburg found that returns not
explained by fundamental cash-flow news were more likely to reverse in the
short run. A directly relevant later study found that strong quarterly
fundamentals helped identify stronger reversals among prior losers.
([Da, Liu, and Schaumburg](https://doi.org/10.1287/mnsc.2013.1766);
[Zhu, Sun, and Chen](https://doi.org/10.1016/j.jempfin.2019.02.006))

## First market feature set

Every feature should be anchored to the qualifying event:

```text
day 1 close -> day 2 close identifies the drop
day 3 open  -> earliest modeled entry
```

| Feature | Deterministic calculation | Why it helps | Initial role |
|---|---|---|---|
| Market-relative drop | Stock day-2 return minus SPY day-2 return | Separates a stock shock from a broad selloff | High |
| Industry-relative drop | Stock return minus sector/industry return | Removes common industry news and shocks | High |
| Overnight component | Day-2 open / day-1 close − 1 | Distinguishes a gap caused by information arriving while closed | High |
| Intraday component | Day-2 close / day-2 open − 1 | Distinguishes continued selling during the session | High |
| Volatility-normalized drop | Event return divided by trailing realized volatility or ATR | A 7% move means something different for a bank and a biotech | High |
| Early recovery | 1-, 3-, and 5-session excess return after the signal | Measures stabilization instead of assuming it | High once those sessions exist |
| Relative volume | Signal-day volume / trailing 20-day median volume | Identifies an abnormal participation shock | Medium |
| Dollar-volume liquidity | Median price × volume over 20 sessions | Helps distinguish liquid repricing from fragile trading | Medium |
| Close versus daily VWAP | Close / VWAP − 1 | Shows whether the close finished below the day's traded average | Low/confirming |
| RSI(14) and RSI change | Wilder RSI and change from signal day | Compactly describes recent loss momentum and stabilization | Low/confirming |
| Range position | (close − 20-day low) / (20-day high − low) | Describes whether price remains pinned near its low | Low/confirming |
| 20- and 60-day trend | Price return over each horizon | Separates a one-day shock from a persistent downtrend | Medium |

There is empirical support for separating overnight and intraday moves: Miwa
reports that intraday returns, rather than overnight returns, drove the
following week's reversal in the studied sample, with stronger reversal among
less-liquid and more-volatile stocks.
([Miwa](https://doi.org/10.1142/S2010139219500022))

Industry adjustment also has a strong conceptual and empirical fit. Firms in
the same industry share many economic and regulatory shocks, so the stock's
residual decline is closer to the overreaction question than its raw decline.
The fundamental-strength reversal study explicitly reports robustness to
industry-adjusted returns.
([Zhu, Sun, and Chen](https://doi.org/10.1016/j.jempfin.2019.02.006))

### RSI

RSI is a technical momentum oscillator, not a fundamental metric. It will often
be low immediately after the very drop that selected the stock, making it
partly redundant with the signal. Do not award recovery points simply because
RSI is below 30. A better use is:

- preserve the continuous RSI value;
- calculate whether RSI stabilized or rose after the event;
- let the backtest determine useful ranges without searching dozens of
  thresholds;
- cap its influence so it cannot override fundamental deterioration.

This restraint matters because testing many indicator rules and keeping the
best result introduces data-snooping bias. Sullivan, Timmermann, and White
demonstrated this problem across a large universe of technical rules.
([Sullivan, Timmermann, and White](https://www.fmg.ac.uk/publications/discussion-papers/data-snooping-technical-trading-rule-performance-and-bootstrap))

### VWAP and volume

Assuming “vmap” meant VWAP, Alpaca already returns `volume`, `trade_count`, and
`vwap` on each bar, although Recovery Trader currently discards them. Adding
them to `DailyBar` does not require another market-data request.
([Alpaca Bar documentation](https://alpaca.markets/sdks/python/api_reference/data/models.html))

Useful derived fields are signal-day relative volume, close-to-VWAP percentage,
20-day median dollar volume, and a properly volume-weighted multi-day VWAP:

```text
rolling VWAP = sum(daily VWAP × daily volume) / sum(daily volume)
```

Do not treat VWAP as fair value. It is a trading benchmark, and a close below
VWAP does not prove that a stock is oversold. Also lower the confidence of
volume-based features under Alpaca Basic: its free IEX feed is one exchange and
Alpaca says it represents roughly 2.5% of U.S. market volume, whereas SIP is
consolidated across U.S. exchanges.
([Alpaca data-source documentation](https://docs.alpaca.markets/us/docs/historical-stock-data-1))

## First fundamental feature set

The application already collects revenue, operating income, net income, EPS,
operating cash flow, capex, debt, cash, and diluted shares. Derive a compact
quality brief from these before adding more raw facts:

| Feature | Calculation | Interpretation |
|---|---|---|
| Revenue growth | YoY revenue change | Demand direction, not profitability |
| Operating margin | Operating income / revenue | Core operating profitability |
| Margin change | Current margin minus prior-year margin | Improving or deteriorating operating economics |
| Free cash flow | Operating cash flow − capex | Cash remaining after reported investment |
| Free-cash-flow margin | Free cash flow / revenue | Cash conversion scaled for company size |
| Cash conversion | Compare operating cash flow with net income | Flags earnings less supported by operating cash |
| Net debt | Debt − cash | Balance-sheet pressure; evaluate by sector |
| Share dilution | YoY diluted-share change | Per-share headwind or buyback support |

The next SEC extension should add total assets, gross profit or cost of revenue,
current assets, and current liabilities. That makes a quarterly financial-
strength score possible:

- positive net income and operating cash flow;
- improving return on assets;
- operating cash flow greater than net income;
- falling leverage and improving current ratio;
- no share dilution;
- improving gross margin and asset turnover.

These are the nine families behind Piotroski's F-score. The original study
showed useful separation among high book-to-market firms, primarily over a
one-year horizon, so Recovery Trader should label this a **financial-strength
filter**, not claim that the original F-score predicts a 30-day bounce.
([Piotroski paper](https://www.ivey.uwo.ca/media/3775523/value_investing_the_use_of_historical_financial_statement_information.pdf))

Profitability also has broader asset-pricing support, but that evidence concerns
differences in average returns rather than a precise 30-day target. It supports
using profitability as context, not giving it dominant timing weight.
([Fama and French](https://doi.org/10.1016/j.jfineco.2014.10.010);
[Novy-Marx](https://www.nber.org/papers/w15940))

SEC Company Facts is appropriate for this layer: it returns standardized XBRL
facts in JSON, requires no API key, and is updated as filings are disseminated.
Additional tags can generally be extracted from the same company-facts response,
so the first extension should not increase the number of SEC requests. Missing
and custom-tagged facts still need explicit coverage handling.
([SEC EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces))

## Proposed weighting architecture

Do not optimize exact weights yet. Start with a frozen, interpretable hypothesis
and revise it only through walk-forward backtesting.

Within the existing 30% market category:

| Market component | Initial share of market category |
|---|---:|
| Market/industry-relative shock | 30% |
| Post-drop stabilization and excess recovery | 25% |
| Volatility-normalized severity | 15% |
| Volume and liquidity | 15% |
| Medium-term trend | 10% |
| RSI and VWAP confirmation | 5% |

Within the existing 25% earnings/fundamental category:

| Fundamental component | Initial share of directional evidence |
|---|---:|
| Operating trend: revenue and margins | 30% |
| Cash-flow and earnings quality | 30% |
| Balance-sheet resilience | 25% |
| Dilution and investment efficiency | 15% |

Continue treating data freshness and coverage as confidence, not direction. A
stale excellent quarter should contribute less evidence, but its reported
results should not be changed. Sector exceptions should continue to suppress
inapplicable ratios rather than convert missing information into a neutral or
negative fundamental signal.

Qwen should explain the deterministic features, identify contradictions, and
summarize the supplied evidence. Python should calculate the directional market
and fundamental contributions so identical inputs produce identical scores.

## Implementation order

1. Extend `DailyBar` and the Alpaca parser with volume, trade count, and VWAP.
   This uses fields already returned by the current request.
2. Create a pure `MarketFeatures` calculator anchored to the latest qualifying
   close-to-close drop. Include SPY and a sector reference in the same batched
   request where practical.
3. Implement the first market feature table and evidence coverage. Leave RSI
   and VWAP at a combined 5% of the market category.
4. Extend SEC fact selection with total assets, gross profit, current assets,
   and current liabilities, then calculate the financial-strength fields.
5. Show the deterministic features and score beside the existing raw prompt
   preview before allowing them to affect the production score.
6. Run point-in-time, walk-forward tests by sector and event type. Freeze the
   feature definitions before comparing weights, include trading frictions, and
   keep a final untouched test period.

## Important limitations

- High volume can represent forced liquidity selling or informed reaction to
  news. The literature does not support assigning it one universal direction.
- RSI and VWAP are derived from the same price event and can double-count the
  drop if given too much weight.
- IEX volume/VWAP is incomplete relative to the consolidated U.S. market.
- SEC facts are quarterly and may be stale relative to the drop; filing
  availability dates must be enforced in historical tests.
- Analyst earnings surprise, estimate revisions, and confirmed future earnings
  dates are not supplied by SEC Company Facts and need another point-in-time
  data source.
- These weights are an initial model specification, not validated trading
  parameters or investment advice.
