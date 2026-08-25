"""Structured Ollama research reports and deterministic confidence scoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from recovery_trader.integrations.ollama import OllamaClient
from recovery_trader.research.context import ResearchContext

CATEGORIES = ("market", "earnings", "news", "macro", "regulation", "sentiment")
RATING_VALUES = {"negative": 0, "neutral": 50, "positive": 100}
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
    score: int
    assessments: dict[str, CategoryAssessment]
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    uncertainties: tuple[str, ...]


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
    """Calculate an equal-weight 0-100 score from validated category ratings."""
    return round(sum(RATING_VALUES[item.rating] for item in assessments.values()) / len(assessments))


def parse_report(raw_response: str, ticker: str) -> ResearchReport:
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

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Ollama report must contain a summary.")
    lists: dict[str, tuple[str, ...]] = {}
    for field in ("catalysts", "risks", "uncertainties"):
        value = payload.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"Ollama report field '{field}' must be a list of non-empty strings.")
        lists[field] = tuple(item.strip() for item in value)

    if missing_categories:
        missing = ", ".join(category.title() for category in CATEGORIES if category in missing_categories)
        lists["uncertainties"] = (*lists["uncertainties"], f"The local model did not assess: {missing}.")

    return ResearchReport(ticker.strip().upper(), summary.strip(), confidence_score(assessments), assessments, lists["catalysts"], lists["risks"], lists["uncertainties"])


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
    return parse_report(raw_response, context.ticker)
