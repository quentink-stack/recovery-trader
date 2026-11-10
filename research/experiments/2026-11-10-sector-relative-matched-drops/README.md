# 2026-11-10: Raw-drop-matched sector-relative outcomes

Status: **completed post-hoc exploratory analysis; all evaluation data already inspected**.

## Question and hypothesis

After controlling more directly for the stock's absolute signal-day decline,
do extremely company-specific drops still have weaker 5-, 10-, 20-, and
30-session outcomes than less company-specific drops? This follows the
[sector-relative quartile experiment](../2026-11-10-sector-relative-drops/README.md),
where the extreme group also contained substantially larger raw declines.

## Protocol before viewing matched outcomes

- Reuse the 2,020-event non-overlapping cohort and sector-relative fields in
  `exports/sector-relative-20261110-144044-607631/`. No new Alpaca or Qwen call.
- Treatment is the previously frozen older-period Q1 cutoff:
  sector-relative excess `<= -7.354168958370034` percentage points. Controls
  are all less-negative observations.
- Match without replacement within the same older/recent period and GICS
  sector. Require absolute stock-drop difference `<= 1.0` percentage point and
  signal dates no more than 180 calendar days apart. Matching uses no forward
  return or recovery field.
- Process treatment observations with the fewest eligible controls first, then
  choose the control with the smallest raw-drop difference, calendar gap,
  ticker, and signal date in that order. Preserve unmatched observations as a
  reported limitation; do not widen calipers after seeing outcomes.
- Compare 5-, 10-, 20-, and 30-session net returns already calculated with 10
  basis points per side. Report treatment/control medians, paired differences,
  win rates, 10th percentiles, 30-session recovery, treatment outperformance,
  match balance, and deterministic pair-bootstrap intervals. Bootstrap
  intervals are descriptive and not cluster-robust significance tests.
- Require at least 30 pairs per period and 100 total. A potentially useful risk
  relationship requires an adverse treatment median paired difference of at
  least 2 percentage points at both 20 and 30 sessions in both periods, at least
  a 10-point lower recovery rate, and no offsetting improvement in downside.
  Otherwise classify it as weak, mixed, or null.
- This remains reused current-constituent stock-price data. It is not an unseen
  holdout, causal estimate, earnings-only sample, options test, or portfolio.

## Reproduction and provenance

- Command:

  ```powershell
  python -m scripts.analyze_sector_relative_matched exports/sector-relative-20261110-144044-607631
  ```

- Execution revision: `fa5c25ed55645f2b4de5459576112c200ac0e9db` with
  both sector-relative experiments, their analysis scripts, and test changes
  uncommitted. The implementation was dirty and is identified by the committed
  code plus the recorded hashes.
- Python 3.14.3, pandas 2.3.3, NumPy 2.5.1. No network request was made.
- Source: ignored local directory
  `exports/sector-relative-20261110-144044-607631/`. Output:
  `exports/sector-relative-matched-20261110-145414-265716/`.
- Source SHA-256: `events_with_sector.csv`
  `7bbd2c38f39397908bbd06beb785496a4f9c3be7a34b5f0e0cf23e37ca0f90c6`;
  source `metadata.json`
  `c2f5c66405137be939aa320ed7599de2a2890ba441afb8e1924fe7d56f6edf6f`.
- Local result SHA-256: `summary.csv`
  `53a5851f12a4e6d90f13825396d5de25d4534fde7ae7d7d435243c8ab1c77c81`;
  `by_sector.csv`
  `f0ac20b29d9d1d9d2407881ec5dc972b538f29e460af8dc09605402cd138d434`;
  `pairs.csv`
  `58c7372a379fc2827dde9eefa1dd9006d9a02e0632c89c24189c3badc18fe49c`;
  `metadata.json`
  `75344cd2d477a9cd5707a74a8b23f7ac681bf967b3cb5dd9c8ecdcf966fdf6e8`.
- The committed [summary.csv](summary.csv) contains every aggregate horizon.
  [match_balance.csv](match_balance.csv) preserves the raw-drop and date
  balance. Pair-level rows and the larger sector breakdown remain local and
  ignored. No private backup was created or verified.
- Committed aggregate SHA-256: `summary.csv`
  `ec58d9d5b73226d64a43abd70edf95a47fa4edaa6e9d9509f4438b52dff5cbc9`;
  `match_balance.csv`
  `9095d89642740ec0fabcd6f445d665c050f83f080905f90a779f179a5d5920f3`.
- `python -B -m unittest discover -s tests` passed all 92 tests.

## Results

Of 508 extreme events, 243 were matched: 162 older and 81 recent. The other 265
had no unused control satisfying the fixed period, sector, one-point raw-drop,
and 180-day rules. Median raw-drop differences were only 0.19 points older and
0.27 recent; 90% were within 0.81 and 0.82 points. Median calendar gaps were 98
and 56 days.

Extreme company-specific events underperformed their matched controls at every
horizon in both periods. At 20 sessions, median paired differences were -6.47
points older and -4.04 recent. At 30 sessions they were -11.25 older and -3.51
recent. Treatment versus control 30-session medians were -1.81% versus +9.99%
older and -1.88% versus +1.79% recent.

Recovery rates were also lower: 33.33% versus 64.20% older and 37.04% versus
55.56% recent. Extreme events beat their matched control only 30.25% of the time
at 30 sessions older and 41.98% recently.

The older pair-bootstrap intervals for median differences stayed below zero at
20 and 30 sessions. Recent intervals were much wider and crossed zero: -8.17 to
+1.92 points at 20 sessions and -11.21 to +1.81 at 30. Recent downside was mixed:
the treatment 20-session 10th percentile (-13.59%) was better than the control
(-16.03%), while its 30-session 10th percentile was worse (-17.82% versus
-14.83%). Thus the direction and median magnitude meet most predeclared criteria,
but the recent uncertainty and mixed downside prevent a validation-grade result.

## Interpretation and limitations

Controlling raw drop magnitude did not erase the adverse relationship. This
strengthens the interpretation of extreme sector underperformance as a
potential risk flag rather than extra rebound opportunity. It does not prove
causality: matching retained fewer than half the extreme observations, calendar
gaps remain, and only older Industrials and Information Technology had at least
30 sector-specific pairs. No recent sector had 30 pairs.

The recent sample is directionally consistent but imprecise, and its lower tail
is not uniformly worse. Pair matching cannot remove event type, earnings/news,
liquidity, company quality, correlated-date, current-membership, ETF-composition,
or IEX-price effects. Bootstrap ranges are pair-resampled descriptions, not
cluster-robust significance claims.

## Decision and next question

Treat extreme sector-relative underperformance as a promising low-to-moderate
confidence **risk indicator**, not a positive recovery signal. Do not yet change
Qwen or the recovery score because the data are reused/post-hoc, match coverage
is 47.8%, and recent uncertainty remains material.

The next valid confirmation is frozen prospective or genuinely unseen data
using this same cutoff and matching logic. A separate interaction can later test
whether earnings/news evidence explains which extreme events recover, but it
must not be optimized on these same outcomes and called validation.
