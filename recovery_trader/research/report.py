"""Structured Ollama research reports and deterministic confidence scoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from recovery_trader.integrations.ollama import OllamaClient
from recovery_trader.research.context import ResearchContext

CATEGORIES = ("market", "earnings", "news", "macro", "regulation", "sentiment")
RATING_VALUES = {"negative": 0, "neutral": 50, "positive": 100}
CATEGORY_WEIGHTS = {
    "market": 30,
    "earnings": 25,
    "news": 20,
    "macro": 10,
    "sentiment": 10,
    "regulation": 5,
}
RATING_ALIASES = {
    "bearish": "negative",
    "bullish": "positive",
    "mixed": "neutral",
    "uncertain": "neutral",
    "unknown": "neutral",
}


@dataclass(frozen=True)
class CategoryAssessment:
    rating: str
    evidence: str


@dataclass(frozen=True)
class ResearchReport:
    ticker: str
    summary: str
    recovery_score: int
    evidence_coverage: int
    assessments: dict[str, CategoryAssessment]
    category_coverage: dict[str, int]
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    uncertainties: tuple[str, ...]

    @property
    def score(self) -> int:
        """Backward-compatible alias for the directional recovery score."""
        return self.recovery_score


def build_prompt(context: ResearchContext) -> str:
    """Build a grounded prompt that limits the model to supplied evidence."""
    evidence = json.dumps(context.to_payload(), indent=2)
    return f"""You are a cautious equity research assistant. Analyze {context.ticker} using only the evidence below.
Do not invent facts, events, prices, sources, or earnings information. If evidence is missing, say so in uncertainties.
Include every required score category even when the evidence is unavailable; use a neutral rating and say that no relevant evidence was provided.
Return JSON only, with exactly this shape:
{{
  \"summary\": \"brief balanced assessment\",
  \"score_categories\": {{
    \"market\": {{\"rating\": \"positive|neutral|negative\", \"evidence\": \"...\"}},
    \"earnings\": {{\"rating\": \"positive|neutral|negative\", \"evidence\": \"...\"}},
    \"news\": {{\"rating\": \"positive|neutral|negative\", \"evidence\": \"...\"}},
    \"macro\": {{\"rating\": \"positive|neutral|negative\", \"evidence\": \"...\"}},
    \"regulation\": {{\"rating\": \"positive|neutral|negative\", \"evidence\": \"...\"}},
    \"sentiment\": {{\"rating\": \"positive|neutral|negative\", \"evidence\": \"...\"}}
  }},
  \"catalysts\": [\"...\"],
  \"risks\": [\"...\"],
  \"uncertainties\": [\"...\"]
}}

Evidence:
{evidence}
"""


def confidence_score(assessments: dict[str, CategoryAssessment]) -> int:
    """Calculate a weighted 0-100 research score from validated ratings."""
    total_weight = sum(CATEGORY_WEIGHTS[category] for category in assessments)
    if total_weight == 0:
        raise ValueError("At least one category assessment is required to calculate a score.")
    weighted_total = sum(RATING_VALUES[assessment.rating] * CATEGORY_WEIGHTS[category] for category, assessment in assessments.items())
    return round(weighted_total / total_weight)


def category_evidence_coverage(context: ResearchContext) -> dict[str, int]:
    """Score the available source evidence for each research category."""
    market_coverage = 0
    if context.market is not None:
        market_coverage = min(100, round(context.market.bar_count / 20 * 100))

    news_coverage = 0
    if context.news:
        article_count = len(context.news)
        distinct_publishers = len({article.publisher.strip().lower() for article in context.news if article.publisher.strip()})
        dated_articles = sum(article.published_at is not None for article in context.news)
        article_points = min(article_count / 5, 1) * 50
        publisher_points = min(distinct_publishers / 3, 1) * 25
        date_points = dated_articles / article_count * 25
        news_coverage = round(article_points + publisher_points + date_points)

    return {
        "market": market_coverage,
        "earnings": 0,
        "news": news_coverage,
        "macro": 0,
        "regulation": 0,
        "sentiment": round(news_coverage * 0.5),
    }


def evidence_coverage_score(category_coverage: dict[str, int]) -> int:
    """Calculate weighted overall evidence coverage on a 0-100 scale."""
    total_weight = sum(CATEGORY_WEIGHTS.values())
    weighted_total = sum(CATEGORY_WEIGHTS[category] * max(0, min(100, category_coverage.get(category, 0))) for category in CATEGORIES)
    return round(weighted_total / total_weight)


def parse_report(
    raw_response: str,
    ticker: str,
    *,
    category_coverage: dict[str, int] | None = None,
) -> ResearchReport:
    """Validate Ollama JSON and calculate its score without trusting model arithmetic."""
    try:
        payload: Any = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ValueError("Ollama report was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Ollama report must be a JSON object.")

    raw_categories = payload.get("score_categories")
    if not isinstance(raw_categories, dict):
        # Smaller local models sometimes emit the category objects at the JSON
        # root instead of nesting them below score_categories. Treat that as a
        # recoverable schema variation rather than discarding the full report.
        raw_categories = {category: payload[category] for category in CATEGORIES if category in payload}
    normalized_categories = {str(category).strip().lower(): value for category, value in raw_categories.items()}
    missing_categories = set(CATEGORIES) - set(normalized_categories)
    assessments: dict[str, CategoryAssessment] = {}
    for category in CATEGORIES:
        if category in missing_categories:
            assessments[category] = CategoryAssessment("neutral", "The local model did not provide an assessment for this category.")
            continue
        item = normalized_categories[category]
        if not isinstance(item, dict):
            raise ValueError(f"Invalid Ollama assessment for {category}.")
        rating = str(item.get("rating", "")).strip().lower()
        rating = RATING_ALIASES.get(rating, rating)
        evidence = item.get("evidence")
        if isinstance(evidence, list):
            evidence = "; ".join(str(value).strip() for value in evidence if str(value).strip())
        elif not isinstance(evidence, str):
            evidence = ""
        if rating not in RATING_VALUES or not evidence.strip():
            raise ValueError(f"Invalid Ollama assessment for {category}.")
        assessments[category] = CategoryAssessment(rating, evidence.strip())

    model_omissions: list[str] = []
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        alternate_summary = next(
            (
                payload.get(field)
                for field in ("analysis", "overview")
                if isinstance(payload.get(field), str) and payload.get(field).strip()
            ),
            None,
        )
        if isinstance(alternate_summary, str):
            summary = alternate_summary
        else:
            summary = "The local model did not provide a narrative summary. Review the category assessments and source evidence below."
            model_omissions.append("The local model did not provide a narrative summary.")
    lists: dict[str, tuple[str, ...]] = {}
    for field in ("catalysts", "risks", "uncertainties"):
        value = payload.get(field)
        if isinstance(value, list):
            entries = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
            lists[field] = entries
            if len(entries) != len(value):
                model_omissions.append(f"The local model provided an incomplete {field} list.")
        else:
            lists[field] = ()
            model_omissions.append(f"The local model did not provide a valid {field} list.")

    if missing_categories:
        missing = ", ".join(category.title() for category in CATEGORIES if category in missing_categories)
        lists["uncertainties"] = (*lists["uncertainties"], f"The local model did not assess: {missing}.")
    if model_omissions:
        lists["uncertainties"] = (*lists["uncertainties"], *model_omissions)

    coverage = {category: max(0, min(100, (category_coverage or {}).get(category, 0))) for category in CATEGORIES}
    return ResearchReport(
        ticker.strip().upper(),
        summary.strip(),
        confidence_score(assessments),
        evidence_coverage_score(coverage),
        assessments,
        coverage,
        lists["catalysts"],
        lists["risks"],
        lists["uncertainties"],
    )


def generate_report(
    context: ResearchContext,
    client: OllamaClient,
    *,
    on_stage: Callable[[str], None] | None = None,
) -> ResearchReport:
    """Generate and validate a report for an already-collected research context."""
    if on_stage is not None:
        on_stage("Generating report with local Qwen3")
    raw_response = client.generate(build_prompt(context), json_response=True)
    if on_stage is not None:
        on_stage("Validating the structured report")
    return parse_report(raw_response, context.ticker, category_coverage=category_evidence_coverage(context))
