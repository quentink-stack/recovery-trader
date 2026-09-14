# Return-consistency experiment — September 9, 2026

The [durable experiment record](../research/experiments/2026-09-09-recovery-exits/README.md)
preserves the aggregate CSVs, methodology, hashes, and raw-data backup requirements
in Git-eligible files. Start at [the research index](../research/README.md) for
the current state and future experiments.

The most promising refinement in this dataset is a **close-based recovery exit**,
not the trailing stop. This is an exploratory finding, not an established trading
edge. No app behavior or Qwen scoring was changed.

## What was tested

The offline `scripts/analyze_market_consistency.py` script reused the existing two-year
Alpaca IEX export. The source contained 4,471 qualifying close-to-close drop
events at the 5% threshold. It did not identify or require earnings events.

After excluding 282 incomplete windows, purging 136 windows that reached the
January 15, 2026 older/recent boundary, and removing 2,033 overlapping same-ticker
entries, 2,020 events remained: 1,323 older and 697 recent. No benchmark calendar
gaps were found. Every exit policy uses this same sample. Each ticker is blocked
for a full 30-session window even when an experimental exit occurs sooner.

Entry is the open after the drop closes. Recovery means a subsequent close at
or above the pre-drop reference close; the modeled exit is the following open,
not that already-observed close. Recovery/stop policies otherwise exit at the
30th session's close. Entry counts as session 1. Stops in this experiment are
close-observed conditions, not intraday stop orders or guaranteed loss limits.

## All qualifying drops: net stock returns

Assume 10 basis points (0.10%) of friction on each side of each trade.

| Exit rule | Older median | Recent median | Recent mean | Recent win rate |
|---|---:|---:|---:|---:|
| Hold 20 sessions | 2.50% | 0.19% | 1.51% | 50.50% |
| Hold 30 sessions | 2.61% | 0.39% | 3.16% | 50.65% |
| Recovery close, next-open exit | 5.62% | 4.77% | 1.46% | 60.98% |
| Recovery plus 10% close-based stop | 5.16% | 2.63% | -0.04% | 54.09% |
| 10% closing-price trailing stop | 1.35% | -3.43% | 0.57% | 41.46% |

Recovery improved the median and win rate but cut the mean: it sold some stocks
before their largest gains. It is not simply better on every objective.

## Does the recovery result survive the consistency checks?

- Giving every signal date equal weight, the median of date-level median returns
  was +2.66% older and +4.39% recent.
- After excluding the three most crowded signal dates within each period, its
  median was +3.27% older and +5.34% recent.
- All seven recent sectors with at least 30 retained events had positive median
  returns. The three recent calendar-quarter groups were positive too, although
  the first and last quarter groups are partial.
- Median matched-SPY excess return was +1.89 percentage points older and +3.49
  points recent. **Mean** excess was much smaller: +0.10 and +0.73 points.
- At 25 basis points per side, recovery medians remained +5.31% older and +4.46%
  recent. That cost assumption is a sensitivity test, not an execution estimate.

These checks reduce dependence on individual observations; they do not remove
cross-ticker correlation or establish statistical significance.

## What remains weak

The recent recovery strategy's 10th-percentile trade returned **-14.06%**, its
worst trade **-41.59%**, and **17.07%** of trades lost at least 10%. Its recent
mean return was only +1.46%, despite the +4.77% median. The older 2025 Q1 group
still had a negative recovery median (-3.43%). A recovery exit does not solve
the problem of stocks that keep falling.

The fixed 2-to-5-times-prior-volatility filter produced modest additional median
improvements (recovery: +5.84% older and +5.47% recent), while reducing the sample
to 734 and 418 events. Because the earlier analysis already exposed volatility
buckets, this comparison is exploratory too; it does not independently validate
those thresholds. There was no automated threshold search.

## Next research step

Freeze the simple recovery exit as a candidate and compare it with the unchanged
30-session baseline on genuinely unseen history or future observations. Before
using it for trading, evaluate a capital-constrained portfolio with explicit
position sizing and sector/date exposure limits. Historical earnings-event
classification is a separate experiment; this study cannot tell us whether the
edge exists specifically after earnings.

These are adjusted IEX stock-price experiments on current S&P 500 constituents,
not historical-membership, executable-price, or options backtests. The matched
SPY comparison uses each stock trade's own exit timing; idle cash and reinvestment
after an early exit are not modeled. None of these per-trade statistics is CAGR,
portfolio drawdown, or a forecast of returns.

## Reproduction and artifacts

```powershell
python -m scripts.analyze_market_consistency exports/market-analysis-20260909-152502-884305
python -B -m unittest discover -s tests
```

The final run is in `exports/consistency-20260909-154540-423773/`:
`summary.csv`, `by_quarter.csv`, `by_sector.csv`, `trades.csv`, and `metadata.json`.
The metadata records source-file hashes, exclusions, fixed policies, costs, and
limitations. No additional Alpaca requests or Qwen calls were made. All 81 tests
passed, including 12 new consistency-analysis tests.
