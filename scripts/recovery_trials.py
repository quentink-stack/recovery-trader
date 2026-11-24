"""Prospective stock recovery evaluation. Run with python -m scripts.recovery_trials."""
from scripts import recovery_benchmark as benchmark

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean

from recovery_trader.domain.screener import load_watchlist
from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.integrations.news import NewsClient
from recovery_trader.integrations.ollama import OllamaClient
from recovery_trader.integrations.sec_edgar import SecEdgarClient
from recovery_trader.research.report import build_prompt, generate_report
from recovery_trader.research.sector_benchmarks import sector_benchmark_for_ticker
from recovery_trader.research.service import ResearchService

ROOT = Path(__file__).resolve().parents[1]
HORIZON = 30


def open_store(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS trials (
        ticker TEXT, signal TEXT, forecast TEXT, feed TEXT, snapshot TEXT,
        outcome TEXT, PRIMARY KEY(ticker, signal))""")
    return db


def active_drop(bars):
    for i in range(len(bars) - 1, 0, -1):
        target = bars[i - 1].close
        if bars[i].close <= 0.95 * target:
            if all(bar.close < target for bar in bars[i:]):
                return bars[i - 1], bars[i]
            break
    raise ValueError("No unresolved 5% drop in the saved price window.")


def capture(db, ticker):
    market = AlpacaMarketData.from_config()
    try:
        service = ResearchService(market, NewsClient(), SecEdgarClient.from_config())
    except ValueError as exc:
        service = ResearchService(market, NewsClient(), sec_setup_error=str(exc))
    sector, etf = sector_benchmark_for_ticker(ticker, load_watchlist(ROOT / "data/sp500.csv"))
    context = service.collect(ticker, sector_name=sector, sector_benchmark=etf)
    prior, signal = active_drop(context.market_bars)
    key = (context.ticker, signal.day.isoformat())
    if db.execute("SELECT 1 FROM trials WHERE ticker=? AND signal=?", key).fetchone():
        return "Already captured"
    model = OllamaClient()
    report = generate_report(context, model)
    snapshot = dict(context=asdict(context), prompt=build_prompt(context), report=asdict(report),
                    model=model.config.model, temperature=model.config.temperature,
                    prior_day=prior.day, target=prior.close)
    with db:
        saved = db.execute("INSERT OR IGNORE INTO trials VALUES (?, ?, ?, ?, ?, NULL)",
                           (*key, datetime.now(timezone.utc).isoformat(), market.equities_feed,
                            json.dumps(snapshot, default=str, allow_nan=False)))
    return "Captured" if saved.rowcount else "Already captured"


def label(snapshot, forecast_day, bars, today):
    # Re-read the pre-drop bar so subsequent splits use one adjustment basis.
    closes = {b.day: b.close for b in bars if b.day < today}
    target = closes.get(date.fromisoformat(snapshot["prior_day"]))
    future = sorted(day for day in closes if day > forecast_day)[:HORIZON]
    if not target or len(future) < HORIZON:
        return None
    hit = next((day for day in future if closes[day] >= target), None)
    return dict(recovered=hit is not None, first_recovery=hit, end=future[-1],
                adjusted_target=target, closes=[(day, closes[day]) for day in future])


def resolve(db):
    pending = db.execute("SELECT * FROM trials WHERE outcome IS NULL").fetchall()
    if not pending:
        return
    market = AlpacaMarketData.from_config()
    today = datetime.now(timezone.utc).date()
    for row in pending:
        if row["feed"] != market.equities_feed:
            raise ValueError("Configure the original Alpaca feed to resolve this trial.")
        snapshot = json.loads(row["snapshot"])
        bars = market.daily_bars(row["ticker"], date.fromisoformat(snapshot["prior_day"]), today)
        result = label(snapshot, datetime.fromisoformat(row["forecast"]).date(), bars, today)
        if result:
            with db:
                db.execute("UPDATE trials SET outcome=? WHERE ticker=? AND signal=? AND outcome IS NULL",
                           (json.dumps(result, default=str), row["ticker"], row["signal"]))


def band(value):
    lower = min(value // 20, 4) * 20
    return f"{lower}-{100 if lower == 80 else lower + 19}"


def summary(db):
    groups = defaultdict(list)
    pending = 0
    for row in db.execute("SELECT snapshot, outcome FROM trials"):
        if row["outcome"] is None:
            pending += 1
            continue
        report = json.loads(row["snapshot"])["report"]
        score, coverage = band(report["recovery_score"]), band(report["evidence_coverage"])
        hit = json.loads(row["outcome"])["recovered"]
        for group in ("all", f"score {score}", f"coverage {coverage}", f"score {score}, coverage {coverage}"):
            groups[group].append(hit)
    return dict(pending=pending, completed=len(groups.get("all", [])),
                groups={k: dict(n=len(v), recovered=sum(v), recovery_rate=mean(v))
                        for k, v in sorted(groups.items())})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "exports/recovery-trials.sqlite")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capture").add_argument("ticker")
    commands.add_parser("resolve")
    commands.add_parser("report")
    commands.add_parser("freeze").add_argument("suite")
    replay = commands.add_parser("replay")
    replay.add_argument("suite")
    replay.add_argument("version")
    replay.add_argument("--weights-only", action="store_true")
    compare = commands.add_parser("compare")
    compare.add_argument("suite")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    args = parser.parse_args()
    db = open_store(args.database)
    try:
        if args.command == "freeze":
            result = benchmark.freeze(db, args.suite)
        elif args.command == "replay":
            result = benchmark.replay(db, args.suite, args.version, args.weights_only, ROOT)
        elif args.command == "compare":
            result = benchmark.compare(db, args.suite, args.baseline, args.candidate)
        else:
            result = None
        if args.command == "capture":
            print(capture(db, args.ticker))
        elif args.command == "resolve":
            resolve(db)
        print(json.dumps(result or summary(db), indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()

