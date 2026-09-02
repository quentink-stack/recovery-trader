"""Structured Ollama research reports and deterministic confidence scoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from recovery_trader.integrations.news import ARTICLE_EXCERPT_LIMIT
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
CATEGORY_NAME_ALIASES = {
    "market conditions": "market",
    "market outlook": "market",
    "price action": "market",
    "technical": "market",
    "technical analysis": "market",
    "fundamentals": "earnings",
    "financial performance": "earnings",
    "company financials": "earnings",
    "earnings outlook": "earnings",
    "news and events": "news",
    "headlines": "news",
    "macroeconomic": "macro",
    "macroeconomics": "macro",
    "economic conditions": "macro",
    "regulatory": "regulation",
    "legal": "regulation",
    "policy": "regulation",
    "investor sentiment": "sentiment",
    "market sentiment": "sentiment",
}
MISSING_CATEGORY_EVIDENCE = "The local model did not provide an assessment for this category."
ASSESSMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "rating": {"type": "string", "enum": list(RATING_VALUES)},
        "evidence": {"type": "string"},
    },
    "required": ["rating", "evidence"],
    "additionalProperties": False,
}
REPORT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "score_categories": {
            "type": "object",
            "properties": {category: ASSESSMENT_RESPONSE_SCHEMA for category in CATEGORIES},
            "required": list(CATEGORIES),
            "additionalProperties": False,
        },
        "catalysts": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "score_categories", "catalysts", "risks", "uncertainties"],
    "additionalProperties": False,
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
When an `earnings` object is present, its arithmetic, period alignment, sector exceptions, and confidence were calculated deterministically. Use its findings for direction, treat `evidence_confidence_percent` as strength rather than direction, and do not override excluded or non-comparable metrics.
News records may include a bounded `excerpt` extracted from the linked public article. Treat excerpts as stronger evidence than headlines, but do not infer facts from a headline-only record or claim to have read an article whose excerpt is null.
The `score_categories` value is required. It must be one JSON object (not a list and not a wrapper) with exactly these six keys: market, earnings, news, macro, regulation, and sentiment. Include every required category even when evidence is unavailable; use a neutral rating and say that no relevant evidence was provided.
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


def confidence_score(
    assessments: dict[str, CategoryAssessment],
    category_coverage: dict[str, int] | None = None,
) -> int:
    """Calculate an evidence-adjusted weighted recovery score.

    When coverage is supplied, each directional rating is pulled toward 50
    (neutral) in proportion to its evidence coverage. This makes freshness part
    of earnings contribution without treating age as positive or negative.
    """
    total_weight = sum(CATEGORY_WEIGHTS[category] for category in assessments)
    if total_weight == 0:
        raise ValueError("At least one category assessment is required to calculate a score.")
    weighted_total = 0.0
    for category, assessment in assessments.items():
        rating_value = RATING_VALUES[assessment.rating]
        if category_coverage is not None:
            coverage = max(0, min(100, category_coverage.get(category, 0)))
            rating_value = 50 + (rating_value - 50) * coverage / 100
        weighted_total += rating_value * CATEGORY_WEIGHTS[category]
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
        excerpt_count = sum(bool(article.excerpt) for article in context.news)
        article_points = min(article_count / 5, 1) * 35
        publisher_points = min(distinct_publishers / 3, 1) * 20
        date_points = dated_articles / article_count * 10
        excerpt_points = min(excerpt_count / min(article_count, ARTICLE_EXCERPT_LIMIT), 1) * 35
        news_coverage = round(article_points + publisher_points + date_points + excerpt_points)

    return {
        "market": market_coverage,
        "earnings": context.earnings.confidence if context.earnings and context.earnings.brief else 0,
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


def _canonical_category_name(value: Any) -> str | None:
    """Map common model labels to the application's fixed score categories."""
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())
    if normalized in CATEGORIES:
        return normalized
    alias = CATEGORY_NAME_ALIASES.get(normalized)
    if alias is not None:
        return alias

    # Qwen often adds a descriptive suffix to a requested category, such as
    # "Market analysis" or "Regulatory considerations". Classify those labels
    # without requiring a brittle list of every possible heading.
    if "sentiment" in normalized:
        return "sentiment"
    if any(term in normalized for term in ("regulat", "legal", "policy", "compliance")):
        return "regulation"
    if any(term in normalized for term in ("macro", "economic", "economy")):
        return "macro"
    if any(term in normalized for term in ("earnings", "financial", "fundamental")):
        return "earnings"
    if any(term in normalized for term in ("news", "headline", "event")):
        return "news"
    if any(term in normalized for term in ("market", "technical", "price action")):
        return "market"
    return None


def _extract_category_mapping(value: Any) -> dict[str, Any]:
    """Recover category assessments from dictionary or list-shaped model JSON."""
    if isinstance(value, list):
        categories: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            category = _canonical_category_name(
                item.get(
                    "category",
                    item.get(
                        "name",
                        item.get(
                            "label",
                            item.get("title", item.get("section", item.get("topic", item.get("area")))),
                        ),
                    ),
                )
            )
            if category is not None:
                categories[category] = item.get("assessment", item)
            else:
                categories.update(_extract_category_mapping(item))
        return categories

    if not isinstance(value, dict):
        return {}

    direct_categories = {
        category: item
        for label, item in value.items()
        if (category := _canonical_category_name(label)) is not None
    }
    if direct_categories:
        return direct_categories

    container_names = (
        "score_categories",
        "category_scores",
        "categories",
        "assessments",
        "ratings",
        "scores",
        "evaluation",
        "report",
        "analysis",
        "result",
        "data",
    )
    for container in container_names:
        nested_categories = _extract_category_mapping(value.get(container))
        if nested_categories:
            return nested_categories

    # Local models occasionally add an arbitrary envelope such as
    # `research_output` or `response`. Search nested JSON values as a final
    # recovery path instead of depending on a fixed wrapper name.
    for label, nested_value in value.items():
        if label in container_names:
            continue
        nested_categories = _extract_category_mapping(nested_value)
        if nested_categories:
            return nested_categories
    return {}


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

    normalized_categories = _extract_category_mapping(payload)
    if not normalized_categories:
        field_names = ", ".join(str(key) for key in payload.keys()) or "none"
        raise ValueError(
            "Ollama report did not include any recognizable category assessments. "
            f"Top-level fields returned: {field_names}. Retry report generation so the model can return the required structured analysis."
        )
    missing_categories = set(CATEGORIES) - set(normalized_categories)
    assessments: dict[str, CategoryAssessment] = {}
    for category in CATEGORIES:
        if category in missing_categories:
            assessments[category] = CategoryAssessment("neutral", MISSING_CATEGORY_EVIDENCE)
            continue
        item = normalized_categories[category]
        if not isinstance(item, dict):
            raise ValueError(f"Invalid Ollama assessment for {category}.")
        rating = str(item.get("rating", item.get("score", ""))).strip().lower()
        rating = RATING_ALIASES.get(rating, rating)
        evidence = item.get("evidence", item.get("rationale", item.get("reasoning", "")))
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
        confidence_score(assessments, coverage if category_coverage is not None else None),
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
    raw_response = client.generate(
        build_prompt(context),
        json_response=True,
        response_schema=REPORT_RESPONSE_SCHEMA,
    )
    if on_stage is not None:
        on_stage("Validating the structured report")
    return parse_report(raw_response, context.ticker, category_coverage=category_evidence_coverage(context))
