"""
Guide 5.6: defensive parsing of the model's answer.

parse_response(raw) -> (data, failure_code)

A failure is data about the model, not an exception to swallow: the
caller still saves a row, with needs_human_review=True and the code.
When the JSON parses but is internally inconsistent, the fields that
did parse are returned alongside the code so the reviewer sees them.
"""

import json
import math

from watcher.services.item_codes import format_item_codes

from .facts import clean_facts
from .taxonomy import is_valid_category


UNPARSEABLE_RESPONSE = "UNPARSEABLE_RESPONSE"
UNKNOWN_CATEGORY = "UNKNOWN_CATEGORY"
INVALID_CONFIDENCE = "INVALID_CONFIDENCE"
INVALID_MATERIALITY = "INVALID_MATERIALITY"
MISSING_CATEGORY = "MISSING_CATEGORY"
INCONSISTENT_ANSWER = "INCONSISTENT_ANSWER"


def _extract_json_text(raw):
    text = (raw or "").strip()

    # Models wrap JSON in fences no matter how firmly you ask.
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1].strip()
            if text[:4].lower() == "json":
                text = text[4:].strip()

    # Tolerate a sentence before or after the object.
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]

    return text



def _clean_items(value):
    if isinstance(value, (list, tuple, str)):
        return format_item_codes(value)[:200]
    return ""


def parse_response(raw):
    try:
        data = json.loads(_extract_json_text(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, UNPARSEABLE_RESPONSE

    if not isinstance(data, dict):
        return None, UNPARSEABLE_RESPONSE

    confidence = data.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        return None, INVALID_CONFIDENCE

    category = data.get("category")
    if category is not None and not isinstance(category, str):
        return None, UNKNOWN_CATEGORY
    category = (category or "").strip().upper()

    if category and not is_valid_category(category):
        # A hallucinated category is not a category. Flag it.
        return None, UNKNOWN_CATEGORY

    is_material = data.get("is_material")

    clean = {
        "is_material": is_material if isinstance(is_material, bool) else None,
        "category": category,
        "confidence": float(confidence),
        "reasoning": str(data.get("reasoning") or "")[:2000],
        "body_item_numbers": _clean_items(data.get("body_item_numbers")),
        "extracted_facts": clean_facts(data.get("extracted_facts")),
    }

    if not isinstance(is_material, bool):
        return clean, INVALID_MATERIALITY

    if is_material and not category:
        return clean, MISSING_CATEGORY

    if not is_material and category:
        return clean, INCONSISTENT_ANSWER

    return clean, None
