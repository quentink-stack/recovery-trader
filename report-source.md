# Research source: deterministic market and fundamental features

Audience: Recovery Trader developer

Date: 2026-09-03

Scope: Simple, point-in-time-safe features for forecasting 5–45 trading-day
recovery after a sharp U.S. equity decline. The review prioritizes inputs
available from Alpaca Basic and SEC EDGAR and treats model-generated narrative
as interpretation rather than source data.

## Direct answer

The next feature layer should not be an RSI-led technical score. The strongest
design is a deterministic event record anchored to the qualifying drop, with
market/industry-relative return, overnight-versus-intraday decomposition,
volatility-normalized severity, volume shock, liquidity, and early
stabilization. RSI and VWAP can be retained as low-weight confirmation features.
Quarterly financial strength should be a separate, freshness-adjusted quality
filter built from SEC facts.

## Evidence synthesis

- Da, Liu, and Schaumburg find that price moves unexplained by fundamental cash
  flow news are more likely to reverse than moves linked to fundamental news.
- Zhu, Sun, and Chen find that quarterly financial strength improves the
  identification of short-term reversals; strong-fundamental losers reverse
  more strongly in their sample. They use a quarterly adaptation of Piotroski's
  accounting score.
- Miwa finds that subsequent one-week reversal is associated with intraday,
  rather than overnight, returns and is stronger in less liquid and more
  volatile stocks.
- Industry adjustment is repeatedly useful because it removes common shocks
  that are less likely to represent firm-specific overreaction.
- Alpaca bars already expose volume, trade count, and VWAP, but the application
  currently discards those fields. Alpaca's free IEX feed covers one venue and
  approximately 2.5% of U.S. market volume, so IEX volume and VWAP should have
  lower evidence confidence than price features.
- SEC Company Facts provides point-in-time XBRL facts without an API key and is
  updated as filings are disseminated. Existing Company Facts responses can
  support additional ratios without a separate request when the necessary tags
  are present.
- Piotroski's original F-score evidence is based on value firms and primarily a
  one-year horizon. It should therefore be used as financial-strength context,
  not copied as a proven 30-day trading rule.
- Sullivan, Timmermann, and White show why selecting successful technical-rule
  parameters after testing many alternatives creates data-snooping bias.

## Claim-to-source ledger

| Claim | Source | Date | Confidence / limitation |
|---|---|---:|---|
| Non-fundamental shocks reverse more readily than fundamental-news shocks. | [A Closer Look at the Short-Term Return Reversal](https://doi.org/10.1287/mnsc.2013.1766), Da, Liu, Schaumburg, *Management Science* | 2014 | High; analyst revisions were used to proxy cash-flow news. |
| Quarterly accounting strength helps condition short-term reversal. | [Fundamental strength and short-term return reversal](https://doi.org/10.1016/j.jempfin.2019.02.006), Zhu, Sun, Chen, *Journal of Empirical Finance* | 2019 | High for the study sample; not a guarantee of forward performance. |
| Intraday losses, liquidity, and volatility distinguish reversal behavior. | [Short-Term Return Reversals and Intraday Transactions](https://doi.org/10.1142/S2010139219500022), Miwa, *Quarterly Journal of Finance* | 2019 | Moderate-to-high; study design and horizon differ from this app. |
| Technical-rule selection is vulnerable to data snooping. | [Data Snooping, Technical Trading, Rule Performance, and the Bootstrap](https://www.fmg.ac.uk/publications/discussion-papers/data-snooping-technical-trading-rule-performance-and-bootstrap), Sullivan, Timmermann, White | 1998/1999 | High methodological relevance. |
| Alpaca bars contain volume, trade count, and VWAP. | [Alpaca-py Bar model](https://alpaca.markets/sdks/python/api_reference/data/models.html) | accessed 2026-09-03 | High; official documentation. |
| The free IEX feed represents one exchange and about 2.5% of market volume. | [Alpaca historical stock data](https://docs.alpaca.markets/us/docs/historical-stock-data-1) | accessed 2026-09-03 | High but subject to vendor changes. |
| EDGAR Company Facts exposes filing-derived standardized facts without authentication. | [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | updated 2025-04-08 | High; custom tags and company reporting choices still cause gaps. |
| Piotroski F-score separates stronger from weaker firms in its original value-stock sample. | [Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers](https://www.ivey.uwo.ca/media/3775523/value_investing_the_use_of_historical_financial_statement_information.pdf), Piotroski, *Journal of Accounting Research* | 2000 | High for original scope; mostly one-year horizon and high book-to-market firms. |
| Profitability and investment are established cross-sectional return dimensions. | [A five-factor asset pricing model](https://doi.org/10.1016/j.jfineco.2014.10.010), Fama and French, *Journal of Financial Economics* | 2015 | High; asset-pricing evidence is not a direct 30-day recovery forecast. |

## Gaps and contradictions

- Published findings disagree on whether high volume strengthens reversal or
  indicates informed/news-driven continuation. Volume must be combined with
  event classification and liquidity, not assigned a universal positive sign.
- RSI-specific evidence is much weaker and less directly applicable than the
  reversal, accounting-strength, and industry-adjustment evidence. Its useful
  role here is descriptive and testable, not authoritative.
- VWAP is primarily a price/volume benchmark. A close below VWAP is not, by
  itself, evidence of undervaluation or recovery.
- Analyst earnings surprise and estimate revisions would be useful but are not
  in SEC data. They require a separate point-in-time estimates source.

## Research stopping point

Further broad searches were unlikely to change the first implementation order.
The remaining uncertainty should be resolved with this application's own
walk-forward, point-in-time backtests rather than by adding more indicators.

