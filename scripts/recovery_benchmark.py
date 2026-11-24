"""Paired regression benchmarks over immutable prospective trial snapshots."""
import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from itertools import groupby
from typing import get_args, get_origin, get_type_hints

from recovery_trader.integrations.ollama import OllamaClient
from recovery_trader.research.context import ResearchContext
from recovery_trader.research.report import (
    CategoryAssessment, build_prompt, category_evidence_coverage, confidence_score,
    evidence_coverage_score, generate_report,
)


def restore(annotation, value):
    """Rehydrate the JSON representation of the app's nested evidence dataclasses."""
    if value is None:
        return None
    args = get_args(annotation)
    if type(None) in args:
        return restore(next(t for t in args if t is not type(None)), value)
    if is_dataclass(annotation):
        hints = get_type_hints(annotation)
        return annotation(**{k: restore(hints[k], v) for k, v in value.items()})
    if annotation in (date, datetime):
        return annotation.fromisoformat(value)
    if get_origin(annotation) is tuple:
        return tuple(restore(args[0], item) for item in value)
    return value


def auc(pairs):
    """Probability a positive ranks above a negative, with half credit for ties."""
    negatives = positives = points = 0
    for _, tied in groupby(sorted(pairs), key=lambda pair: pair[0]):
        labels = [label for _, label in tied]
        yes = sum(labels)
        no = len(labels) - yes
        points += yes * (negatives + no / 2)
        negatives += no
        positives += yes
    return points / (positives * negatives) if positives and negatives else None


def metrics(rows):
    directional = [(coverage, (score > 50) == hit) for score, coverage, hit in rows if score != 50]
    return dict(recovery_auc=auc([(score, hit) for score, _, hit in rows]),
                coverage_correctness_auc=auc(directional), directional_calls=len(directional),
                directional_accuracy=sum(correct for _, correct in directional) / len(directional)
                if directional else None)


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS benchmark_cases (
        suite TEXT, ticker TEXT, signal TEXT, PRIMARY KEY(suite,ticker,signal))""")
    db.execute("""CREATE TABLE IF NOT EXISTS benchmark_runs (
        suite TEXT, version TEXT, artifact TEXT, PRIMARY KEY(suite,version))""")


def freeze(db, suite):
    setup(db)
    with db:
        count = db.execute("""INSERT INTO benchmark_cases
            SELECT ?, ticker, signal FROM trials WHERE outcome IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM benchmark_cases WHERE suite=?)""", (suite, suite)).rowcount
    if not count:
        raise ValueError("No completed trials available, or suite name already frozen.")
    return {"suite": suite, "cases": count}


def replay(db, suite, version, weights_only, root):
    setup(db)
    if version == "captured" or db.execute(
        "SELECT 1 FROM benchmark_runs WHERE suite=? AND version=?", (suite, version)
    ).fetchone():
        raise ValueError("Choose an unused version name.")
    cases = db.execute("""SELECT ticker,signal,snapshot FROM trials JOIN benchmark_cases
        USING(ticker,signal) WHERE suite=? ORDER BY ticker,signal""", (suite,)).fetchall()
    if not cases:
        raise ValueError("Freeze a suite of resolved trials first.")
    model = None if weights_only else OllamaClient()
    predictions = []
    for row in cases:
        snapshot = json.loads(row["snapshot"])
        context = restore(ResearchContext, snapshot["context"])
        if weights_only:
            ratings = {k: CategoryAssessment(**v) for k, v in snapshot["report"]["assessments"].items()}
            coverage = category_evidence_coverage(context)
            report = dict(recovery_score=confidence_score(ratings, coverage),
                          evidence_coverage=evidence_coverage_score(coverage))
        else:
            report = asdict(generate_report(context, model))
        predictions.append(dict(ticker=row["ticker"], signal=row["signal"], report=report,
                                prompt=None if weights_only else build_prompt(context)))
    source = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (root / "recovery_trader").rglob("*.py")}
    artifact = dict(predictions=predictions, source_sha256=source, weights_only=weights_only,
                    model=model.config.model if model else None,
                    temperature=model.config.temperature if model else None,
                    num_ctx=model.config.num_ctx if model else None)
    with db:
        db.execute("INSERT INTO benchmark_runs VALUES (?,?,?)", (suite, version, json.dumps(artifact)))
    return compare(db, suite, "captured", version)


def compare(db, suite, baseline, candidate):
    setup(db)
    cases = db.execute("""SELECT ticker,signal,snapshot,outcome FROM trials JOIN benchmark_cases
        USING(ticker,signal) WHERE suite=? ORDER BY ticker,signal""", (suite,)).fetchall()
    if not cases:
        raise ValueError("Unknown suite.")
    results = []
    for version in (baseline, candidate):
        if version != "captured":
            row = db.execute("SELECT artifact FROM benchmark_runs WHERE suite=? AND version=?",
                             (suite, version)).fetchone()
            if not row:
                raise ValueError(f"Unknown version {version!r} for {suite!r}.")
            reports = {(p["ticker"], p["signal"]): p["report"]
                       for p in json.loads(row["artifact"])["predictions"]}
        values = []
        for case in cases:
            report = (json.loads(case["snapshot"])["report"] if version == "captured"
                      else reports[case["ticker"], case["signal"]])
            values.append((report["recovery_score"], report["evidence_coverage"],
                           json.loads(case["outcome"])["recovered"]))
        results.append(metrics(values))
    before, after = results
    return dict(suite=suite, cases=len(cases), recovered=sum(v[2] for v in values),
                baseline=baseline, candidate=candidate, before=before, after=after,
                delta={k: after[k] - before[k] if None not in (before[k], after[k]) else None
                       for k in before})
