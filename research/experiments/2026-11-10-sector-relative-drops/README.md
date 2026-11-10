# 2026-11-10: Sector-relative drop outcomes

Status: **completed exploratory analysis; evaluation data already inspected**.

## Question and hypothesis

Does a stock's signal-day return relative to its GICS sector ETF distinguish
large drops that subsequently recover from those that continue weakening? A
more negative excess return may indicate a company-specific overreaction, but
the competing explanation is durable company-specific information. A less
negative or positive excess return may indicate relative strength inside a
sector shock. No direction is assumed to be favorable before the results.

## Protocol before viewing outcomes

- Baseline universe is the saved current S&P 500 list and adjusted Alpaca IEX
  daily bars from the existing September 9, 2026 export. Sector ETFs use the
  fixed GICS mapping in `recovery_trader/research/sector_benchmarks.py`.
- A signal is a stock close-to-close decline of at least 5%. Entry is the next
  observed session's open. Fixed outcomes use sessions 5, 10, 20, and 30,
  counting the entry session as session 1, with 10 basis points of friction per
  side.
- Require a complete 30-session window. Preserve the prior experiment's split
  at 70% of unique complete signal dates, purge older windows crossing that
  boundary, retain the earliest event per ticker, and block that ticker for 30
  sessions. Exclude events missing either matching sector-ETF close.
- Sector-relative excess is stock signal return minus sector-ETF return over
  the same prior-close to signal-close sessions, in percentage points.
- Primary groups are quartiles calculated from the older period's excess-return
  distribution without using outcomes. Those exact cutoffs are then applied to
  the recent period. Secondary descriptive bands are fixed in advance:
  `<= -8`, `(-8, -4]`, `(-4, -1]`, and `> -1` percentage points.
- Report event/ticker/date counts, mean, median, win rate, 10th percentile,
  worst return, losses of at least 10%, 30-session recovery rate, drawdown, and
  a date-balanced median. Groups below 30 events are descriptive only.
- A potentially useful relationship must have adequate recent samples and show
  a broadly consistent ordering at both 20 and 30 sessions without improving
  the median by hiding materially worse lower-tail results. Mixed ordering,
  sparse groups, or older-only separation is a null/weak result.
- This is an exploratory stock-price event study, not an options, earnings-only,
  or capital-constrained portfolio test. Outcomes and the older/recent periods
  have already been inspected in prior work; the recent period is not an
  untouched holdout.

## Reproduction and provenance

- Command:

  ```powershell
  python -m scripts.analyze_sector_relative exports/market-analysis-20260909-152502-884305
  ```

- Execution revision: `fa5c25ed55645f2b4de5459576112c200ac0e9db` with the
  new experiment record, analysis script, and test untracked. The implementation
  was therefore dirty and is identified by the committed code plus these hashes.
- Python 3.14.3, pandas 2.3.3, adjusted Alpaca IEX data. Source signal dates
  begin September 9, 2024 and data end September 9, 2026 exclusive.
- Existing raw stock input: `exports/market-analysis-20260909-152502-884305/`.
  New local output: `exports/sector-relative-20261110-144044-607631/`.
- Source SHA-256: `events.csv`
  `4daa3e2ab254aded4a4fbeaa8e238f47a23d6dd60956086b5d0270a0c556e531`;
  `daily_bars.csv`
  `78cc73c37375c078e5f7ed8ee3808ea13c8e617c730f1d34515c7ae16292e5fc`;
  source `metadata.json`
  `10f619f590d1bcaa42718f63f4a3414f1483efed3ff1f4dca0bd651ea5173186`.
- Local output SHA-256: `summary.csv`
  `136db2aab4e1d18373925cf2f72455e9b25db4444946d5758a6f207147716761`;
  `by_sector.csv`
  `6899e1d70504968a4c6b79e01b2a488977a4bf8f60582ec0d7f0118ad2e811bc`;
  `events_with_sector.csv`
  `7bbd2c38f39397908bbd06beb785496a4f9c3be7a34b5f0e0cf23e37ca0f90c6`;
  `sector_bars.csv`
  `e78faf36c5b891fc85960be85cabf06347cad2122e2c0f594fd015496e8ae7e5`.
- Local result `metadata.json` SHA-256:
  `c2f5c66405137be939aa320ed7599de2a2890ba441afb8e1924fe7d56f6edf6f`.
- The committed [summary.csv](summary.csv) preserves every aggregate horizon,
  quartile, and fixed-band result. [group_characteristics.csv](group_characteristics.csv)
  records the main raw-drop/sector-return confound. Event rows, sector bars, and
  the larger sector breakdown remain local and Git-ignored. No private backup
  of either raw export directory was created or verified.
- Committed aggregate SHA-256: `summary.csv`
  `9b9a8dc5ca235c1ac95fc05ed0a9aed2f2c59cff1f00748c58f0b5704c26d5bc`;
  `group_characteristics.csv`
  `725a128dcb8d4a00f10db6fadd731cf8d22d1ff118a45cee73f8c08dafb24f99`.
- `python -B -m unittest discover -s tests` passed all 91 tests after the run.

## Results

All 2,020 events from the prior common cohort had matching sector-ETF closes;
no event was lost to a sector benchmark gap. Older-only quartile cutoffs were
`-7.3542`, `-5.1799`, and `-3.2410` percentage points.

The most-negative sector-relative quartile (Q1) was the weakest group in both
periods. At 20/30 sessions, its median net returns were +0.58%/+0.26% older and
-1.61%/-2.72% recent. Its 30-session recovery rates were 27.49% older and
28.25% recent; recent 10th-percentile 30-session return was -17.56%, and 27.68%
of recent Q1 events lost at least 10% by session 30.

The middle groups were materially better than Q1 in the recent period. Q2 had
20/30-session medians of +1.37%/+1.93% and a 55.87% recovery rate; Q3 had
+0.74%/+2.30% and a 63.88% recovery rate. Q4 was very strong older
(+6.78%/+9.02%, 70.39% recovery) but did not reproduce that magnitude recently
(-0.61%/+0.47%, 62.50% recovery). Thus the recent result is not monotonic across
all four groups, even though the extreme company-specific tail remained weak.

The predefined `> -1` percentage-point band was especially unstable: 114 older
events had an +11.94% median at 30 sessions, while only 10 recent events existed
and their median was -4.90%. That recent group fails the minimum sample rule and
cannot support a favorable relative-strength conclusion.

## Interpretation and limitations

The evidence argues against treating a larger company-specific dislocation as
automatically more favorable. Extremely negative sector-relative drops behaved
more like a risk flag than extra rebound potential in this reused sample.
However, the least-negative quartile was not stable enough to become a positive
rule, so the experiment does not justify a simple preference for broad-sector
drops either.

Sector-relative excess is confounded with absolute drop severity. Q1's median
stock drop was -10.64% older and -9.66% recent, versus -6.07% and -5.41% for Q4.
Meanwhile Q4's sector ETF itself fell a median -5.01% older and -2.78% recent,
versus only -0.40% and -0.07% in Q1. The grouping successfully distinguishes
large isolated stock shocks from broad sector shocks, but it does not establish
that sector-relative excess alone caused the outcome difference. A follow-up
must compare observations with similar raw drop magnitude and ideally separate
earnings/news events.

The study remains subject to reused outcomes, current-membership survivorship,
current sector labels, correlated dates/tickers, ETF composition effects, IEX
pricing limitations, and the absence of options or portfolio modeling. These
are descriptive comparisons, not significance tests.

## Decision and next question

Do not add a positive sector-relative rule to Qwen or the recovery score. The
preview remains inspection-only. The next experiment should match or stratify
events by raw drop magnitude, then test whether sector-relative excess retains
incremental separation within those comparable drops. Earnings/news event type
is a later interaction, not part of this result.
