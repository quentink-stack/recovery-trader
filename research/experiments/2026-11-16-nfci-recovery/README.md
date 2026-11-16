# 2026-11-16: Point-in-time NFCI and dip recovery

Status: **completed exploratory analysis; all stock outcomes were previously inspected**.

## Question and hypothesis

Does the Chicago Fed National Financial Conditions Index (NFCI), using only
values actually available before the modeled entry, distinguish large-drop
events that recover from those that continue weakening? The candidate risk
hypothesis is that a larger four-week increase in NFCI (tightening financial
conditions) is associated with weaker 20- and 30-session outcomes. No favorable
effect from easing conditions is assumed before results are viewed.

## Protocol before viewing outcomes

- Reuse the 2,020-event non-overlapping stock-price cohort in
  `exports/sector-relative-20261110-144044-607631/events_with_sector.csv`.
  Signals are close-to-close declines of at least 5%; entry is the following
  session's open. Outcomes remain the saved 5-, 10-, 20-, and 30-session net
  stock returns with 10 basis points of friction per side.
- Download NFCI revisions from ALFRED in **Observations by Real-Time Period**
  format. For each event, select the latest revision whose real-time start is
  strictly before the day-3 entry date. This conservative rule avoids assuming
  that a same-day release was available before the modeled open.
- The current feature is the newest weekly NFCI observation available at entry.
  The four-week change is current NFCI minus the most recent available
  observation dated 28 to 35 days earlier. Missing values remain missing; they
  are never replaced by zero.
- Primary grouping uses quartiles of four-week NFCI change calculated from the
  older period without outcomes. Those exact cutoffs are then applied to the
  recent period. The highest-change quartile is the candidate tightening-risk
  group; the lowest-change quartile is the comparison group.
- Secondary descriptive grouping uses the sign of the four-week change:
  `> 0` tightening and `<= 0` stable/easing. NFCI level, raw stock-drop
  magnitude, SPY/sector-relative context where available, and the distribution
  of events across signal dates are reported as balance/context fields, not
  optimized filters.
- Report event, ticker, and unique signal-date counts; mean, median, win rate,
  10th percentile, worst return, losses of at least 10%, 30-session recovery,
  drawdown, and a date-balanced median at each horizon. Resample whole signal
  dates for deterministic descriptive bootstrap intervals so same-date stocks
  are not treated as independent observations.
- A potentially useful adverse relationship requires at least 100 events and
  20 unique signal dates in both extreme quartiles in each period; at least a
  2-percentage-point weaker median for the highest-change quartile at both 20
  and 30 sessions in older and recent periods; at least a 10-point lower
  30-session recovery rate; and no offsetting improvement in lower-tail return.
  Sparse groups, wide recent intervals, inconsistent direction, or older-only
  separation are inconclusive/null.
- This is exploratory reused data. All stock outcomes were inspected in prior
  experiments, so neither period is an untouched holdout. It is not an options,
  earnings-only, causal, or capital-constrained portfolio test.
- NFCI remains inspection-only during this experiment. No Qwen payload,
  evidence coverage, category weights, or recovery-score behavior may change
  without a separate result review and explicit approval.

## Reproduction and provenance

- Command:

  ```powershell
  python -m scripts.analyze_nfci_regimes exports/sector-relative-20261110-144044-607631
  ```

- Execution revision: `61a27871f2dbf757b2703b5b5d9b858b67e74e2a` with
  the NFCI adapter, feature logic, analysis script, app preview, experiment
  record, and tests uncommitted. Unrelated recovery-benchmark/trial files were
  also untracked and were not modified by this work.
- Local output: `exports/nfci-regimes-20261116-160512-490090/`. No private
  backup was created or verified.
- Source SHA-256: `events_with_sector.csv`
  `7bbd2c38f39397908bbd06beb785496a4f9c3be7a34b5f0e0cf23e37ca0f90c6`;
  source `metadata.json`
  `c2f5c66405137be939aa320ed7599de2a2890ba441afb8e1924fe7d56f6edf6f`.
- Local output SHA-256: `nfci_realtime_periods.csv`
  `4ba2f0872a2532db4a03c6e3b19b20c6e589e52813b94ebaa0efcf3fea11d398`;
  `events_with_nfci.csv`
  `9131078dfdb66785f9505f9fee58e099ad46f4e38f691d8bc879269e1efdfc5b`;
  `summary.csv`
  `c700f4897dfdb66093f6cf254be72cd18ab634f0559cf223b154a5b258b5b6c`;
  `extreme_comparison.csv`
  `ecd31915850e4bb8f852f06661375bc7d2e9faf282146c2e557d6c58a7d88c2f`;
  `group_characteristics.csv`
  `e8d280117b86db8d84f53569d81f74839f2762999120b58f2f9f41822dbe7efb`;
  `metadata.json`
  `729b37a98f57aad5f48e580badcb2d74db2170e879209f5dccb44f3a7266ec3a`.
- The committed [extreme comparison](extreme_comparison.csv) preserves every
  primary horizon and clustered interval. The committed
  [group characteristics](group_characteristics.csv) preserves sample and
  balance context. Their SHA-256 hashes are
  `d5bc4467a0b5d9d1771749a3fc91980cda91dd4f9204ed2577c551a917604ca3`
  and `e03cdab3222a1bef7d263a8ac1920004efa4089e05f9adb4fb792c5158c133fa`.
- Python 3.14.3 and pandas 2.3.3. The targeted NFCI tests and all 101 currently
  discovered unit tests passed after implementation.

## Results

All 2,020 saved events received a complete point-in-time NFCI feature. The
older-period four-week-change quartile cutoffs were `-0.05032`, `-0.01542`, and
`0.01458`. The sample contained 93 NFCI observation weeks and 355 unique signal
dates.

The pre-registered adverse hypothesis failed. For the most-tightening quartile
minus the least-tightening quartile, median-return differences were only
`-0.38` points at 20 sessions and `+0.55` at 30 sessions in the older period.
Both clustered intervals crossed zero. In the recent period the differences
instead favored the most-tightening quartile: `+5.69` points at 20 sessions and
`+9.50` at 30 sessions. The recent 20-session interval still crossed zero,
while the 30-session interval was `+1.39` to `+19.47` points.

Recovery rates also ran opposite the candidate risk hypothesis. Most-tightening
versus least-tightening recovery was 57.53% versus 43.22% older and 65.14%
versus 44.38% recent. However, the four quartiles were not monotonic: older Q3
was far stronger than Q4 at 20/30 sessions, while recent Q3 was weaker than Q4.
The fixed positive-versus-nonpositive change split likewise favored tightening,
but it is secondary and does not repair the unstable quartile ordering.

Every event occurred while the NFCI level itself was below zero, ranging from
`-0.6668` to `-0.42028`. The experiment therefore compares changes within a
looser-than-average regime; it does not test genuinely tight financial
conditions.

## Interpretation and limitations

The result rejects the proposed use of a rising NFCI as a simple adverse risk
flag in this cohort. It also does not validate the opposite positive rule. The
non-monotonic buckets, wide clustered intervals, reused outcomes, and absence
of any above-zero NFCI regime make a favorable interpretation vulnerable to
date composition and market-regime confounding.

Additional limitations include current-constituent survivorship, correlated
events on the same signal date, a roughly two-year market window, and no
options-pricing model. ALFRED removes revision look-ahead for NFCI, but it cannot
make the reused stock outcomes an unseen evaluation.

## Decision and next question

Do not send NFCI to Qwen, assign it evidence coverage, or give it a 5% recovery
weight. Keep the app preview inspection-only until the user decides whether it
is still useful. A new score experiment would require broader history spanning
both tight and loose NFCI regimes and a separately pre-registered unseen test;
do not reinterpret this same cohort as validation of a positive effect.
