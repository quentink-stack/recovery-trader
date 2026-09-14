# 2026-09-09: Recovery-exit consistency

Status: **exploratory; data already inspected**. No live scoring or app strategy
change was made. This record archives the completed experiment; it is not a
claim that its protocol was registered before the source data was examined.

## Question

Can a small fixed set of exit rules make large-drop stock returns more consistent
across time and sectors, compared with fixed holding periods?

## Findings and decision status

Read the [full findings](../../../docs/market-consistency-findings.md) for the
interpretation. Recovery-at-close with next-open exit improved the older/recent
median returns to +5.62%/+4.77%, compared with +2.61%/+0.39% for a 30-session hold
(all drops, 10 basis points per side). Recent mean return fell from +3.16% to
+1.46%; the recovery strategy's worst recent trade lost 41.59%.

This supports further testing, not deployment. The 10% close-based trailing stop
looked worse on recent median returns. An unseen evaluation and a portfolio-level
risk analysis remain pending; earnings-only and options outcomes were not tested.

## Preserved results

- [summary.csv](summary.csv): every fixed policy/filter/cost/period comparison.
- [by_sector.csv](by_sector.csv): sector comparisons, including weak results.
- [by_quarter.csv](by_quarter.csv): quarter comparisons, including partial quarters.
- [methodology.json](methodology.json): original rules, counts, and limitations;
  the source location is made repository-relative for portability/privacy.
- [manifest.json](manifest.json): artifact and raw-file hashes, local locations,
  and implementation-at-archival hashes. A clean execution-time code revision was
  not captured for this historical run.

CSV values are copied without recalculating or rounding. Percent columns use
percentage units (5 means 5%); excess returns are percentage-point differences.
Empty cells remain missing, not zero. The aggregate tables are small enough for
ordinary Git and contain no credentials or account records.

## Data and reproduction

Source: adjusted Alpaca IEX bars, saved current S&P 500 universe (503 symbols),
signal window beginning September 9, 2024 and data ending September 9, 2026
exclusive. The initial 4,471 events used a 5% close-to-close drop threshold.
After exclusions, 2,020 events remained (1,323 older / 697 recent); the split
boundary was January 15, 2026. The earlier export supplied the five price features.

The exact input files remain under:

```text
exports/market-analysis-20260909-152502-884305/
```

The original detailed results, including `trades.csv`, remain under:

```text
exports/consistency-20260909-154540-423773/
```

Both are ignored by Git. No external backup has been created or verified. Back up
these two folders privately for exact reproduction; the committed aggregates
alone cannot recreate every trade or raw bar.

The archived run used the original root-level command:

```powershell
python analyze_market_consistency.py exports/market-analysis-20260909-152502-884305
```

After the scripts-directory refactor, the equivalent command from the repository root is:

```powershell
python -m scripts.analyze_market_consistency exports/market-analysis-20260909-152502-884305
python -B -m unittest discover -s tests
```

The analysis creates a new export folder without overwriting the original and
does not call Alpaca or Qwen. The recorded run passed 81 tests, including 12
consistency tests. Runtime package versions were not captured at execution.

To verify a restored input against its manifest hash:

```powershell
Get-FileHash -Algorithm SHA256 exports/market-analysis-20260909-152502-884305/daily_bars.csv
```

Compare with that file's `sha256` in `manifest.json`. Restore exact backup bytes;
a fresh download need not match. Commit the analysis implementation/tests together
with this record if they are still untracked; the manifest distinguishes code
captured at archival from a run-time code revision.
