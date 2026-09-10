# Recovery Trader backlog

This is an analysis-and-research backlog, not a list of trading recommendations.
Use it to capture ideas first; prioritize only after deciding that the data and
measurement method are reliable enough.

Completed findings and experiment history are recorded in
[`research/README.md`](research/README.md). Proposed scoring changes remain
inactive until they are validated and explicitly approved.

## Now: make Qwen's evidence more precise

- [ ] Trim the remaining prompt-only fields that do not alter Qwen's analysis
  or a deterministic score. Candidates: `market.bar_count`, the duplicate SEC
  brief availability date, and the estimated-next-earnings fields.
- [ ] Show prompt size before generation: character count, approximate token
  count, number of readable article excerpts, and the configured model context
  window. Warn when it is likely to crowd out the response.
- [ ] Aim to supply three usable article excerpts from the nine news candidates.
  Record why extraction fails, continue through candidates until three excerpts
  are found or all nine are exhausted, and try bounded fallbacks such as JSON-LD
  or an Open Graph description while preserving timeout, size, sanitization, and
  paywall safeguards.
- [ ] Deduplicate syndicated or substantially similar news so Qwen does not
  treat the same event as independent confirmation.
- [ ] Apply a recency policy to news and explicitly label undated articles as
  low-confidence evidence.
- [ ] Make Qwen state the supplied evidence it relied on for each category,
  rather than allowing a generic rationale.

## Improve deterministic analysis before asking Qwen

- [ ] Reevaluate the market category and its 30% recovery-score weight. Trace
  exactly how daily bars become the market payload, Qwen rating, market evidence
  coverage, and final score contribution. Compare the current raw summary with
  the five previewed deterministic features before proposing or testing changes.
- [ ] Add price-action features that are defined before model use: distance
  from the 20-day high/low, realized volatility, recovery from the signal low,
  and days since the qualifying drop.
- [ ] Add market-volume features only after confirming a consistent source and
  adjustment policy: signal-day relative volume, follow-through volume, and
  average dollar volume / liquidity.
- [ ] Keep macro, regulation, and sentiment neutral unless a dedicated,
  time-stamped source is actually supplied for that category.
- [ ] Decide whether the expected next earnings date should become an explicit
  event-risk input. It is currently an SEC-cadence estimate, not a company
  confirmed date.
- [ ] Define a deterministic news-confidence score based on publisher
  diversity, article accessibility, recency, and duplication.

## Strengthen SEC earnings evidence

- [ ] Extract a compact, attributable earnings-release summary from the Item
  2.02 / EX-99.1 exhibit, with strict text limits and no full HTML sent to
  Qwen.
- [ ] Reconcile the release with XBRL facts and flag material differences or
  non-GAAP measures rather than silently combining them.
- [ ] Add an earnings-surprise field only after choosing a point-in-time
  estimates data source; SEC filings do not contain consensus estimates.
- [ ] Improve sector-specific rules beyond financials, especially for REITs,
  insurers, and businesses where cash flow or debt comparisons are atypical.
- [ ] Validate earnings conclusions by filing availability date so later facts
  never leak into historical research or backtests.
- [ ] Classify historical earnings events using only information available on
  each signal date, then compare that subset with the broader large-drop sample.

## Backtesting and strategy lab

- [ ] Compare defined entry rules: next open, next close, delayed entry, and
  recovery confirmation, all using point-in-time available signals.
- [ ] Sweep trailing-stop settings by initial stop, trailing percentage or ATR,
  maximum holding period, and profit target; report both return and drawdown.
- [ ] Test a capital-constrained portfolio with realistic opening gaps,
  slippage, commissions where applicable, unfilled stops, position sizing,
  overlapping positions, and sector/date exposure limits.
- [ ] Segment results by market regime, sector, volatility, and drop severity
  instead of relying only on an aggregate win rate.
- [ ] Check survivorship bias in the S&P 500 universe and document whether the
  historical constituent list is point-in-time accurate.
- [ ] Freeze the recovery-exit candidate and unchanged 30-session baseline, then
  evaluate them on an unseen window. Add a paper-trading log before treating any
  parameters as usable.

## Data and application quality

- [ ] Add source timestamps, cache status, and a per-run data provenance
  summary so every report can be reproduced.
- [ ] Add downloadable research snapshots: the exact Qwen payload, report,
  deterministic scores, and source metadata, excluding credentials.
- [ ] Make model settings visible per report: model name, temperature, timeout,
  and whether an output was retried.
- [ ] Add a small evaluation set of tickers and expected evidence outcomes to
  guard against regressions in article parsing, SEC mapping, and report JSON.

## Idea inbox

Add unprioritized ideas here before deciding whether they belong in the
research workflow, deterministic scoring, or a backtest:

- [ ]


## My own ideas not generated from AI
- [ ] Weight volaltility of the stock by sector potentially? pharma vs tech in the current market or something stable like banks etc.
- [ ] Exclude data we send to qwen if it is all null, for example i found earnings data for Debt from EDGAR for sec data, and current, prior year, and yoy was all null. We shoguldn't send this entire section to qwen at all. Unless we should and it contributes to confidence?
