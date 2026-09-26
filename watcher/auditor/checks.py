"""
The two checks, plus the parsing they need.

audit_classification() TAKES A FILING, NEVER A FilingClassification.
The Interpreter's answer is structurally unreachable from inside this
function, which is a stronger guarantee than a comment asking people
not to look at it.
"""

import json

from watcher.interpreter.filing_text import build_filing_text
from watcher.interpreter.service import (
    INPUT_EXHIBIT_PREFIXES,
    MAX_INPUT_CHARS,
)
from watcher.interpreter.taxonomy import is_valid_category

from .grounding import check_claims
from .prompts import (
    build_category_prompt,
    build_claim_prompt,
    build_materiality_prompt,
)


UNPARSEABLE_RESPONSE = "UNPARSEABLE_RESPONSE"
UNKNOWN_CATEGORY = "UNKNOWN_CATEGORY"
INVALID_CONFIDENCE = "INVALID_CONFIDENCE"
NO_FILING_TEXT = "NO_FILING_TEXT"
NO_SUMMARY = "NO_SUMMARY"


def _extract_json(raw):
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1].strip()
            if text[:4].lower() == "json":
                text = text[4:].strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    return text


def parse_materiality(raw):
    """Stage 1. Returns (data, failure_code)."""
    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, UNPARSEABLE_RESPONSE

    if not isinstance(data, dict):
        return None, UNPARSEABLE_RESPONSE

    is_material = data.get("is_material")
    if not isinstance(is_material, bool):
        return None, UNPARSEABLE_RESPONSE

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return None, INVALID_CONFIDENCE
    if not 0.0 <= float(confidence) <= 1.0:
        return None, INVALID_CONFIDENCE

    return {"is_material": is_material, "confidence": float(confidence)}, ""


def parse_category(raw):
    """Stage 2. Returns (data, failure_code)."""
    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, UNPARSEABLE_RESPONSE

    if not isinstance(data, dict):
        return None, UNPARSEABLE_RESPONSE

    category = data.get("category") or ""
    if not is_valid_category(category):
        return None, UNKNOWN_CATEGORY

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return None, INVALID_CONFIDENCE
    if not 0.0 <= float(confidence) <= 1.0:
        return None, INVALID_CONFIDENCE

    return {"category": category, "confidence": float(confidence)}, ""


def build_audit_input(filing):
    """
    Identical to InterpreterService.build_input, on purpose: both models
    must see byte-identical text or the agreement rate measures the
    input difference instead of the model difference.
    """
    return build_filing_text(
        filing,
        max_chars=MAX_INPUT_CHARS,
        exhibit_prefixes=INPUT_EXHIBIT_PREFIXES,
    )


def audit_classification(filing, *, generator):
    """
    Re-read one filing blind, in two stages, and return
    (data, failure_code, sha256).

    Stage 1 decides material vs routine WITHOUT seeing the category
    list. Stage 2 runs only for material filings. A routine filing
    therefore costs one model call, not two.

    Takes a filing. Does not take, and cannot see, the classification
    it is being compared against.
    """
    built = build_audit_input(filing)

    if built.is_empty:
        return None, NO_FILING_TEXT, ""

    text, sha = built.text, built.sha256

    raw = generator.generate(
        build_materiality_prompt(text), temperature=0.0, seed=42
    )
    stage1, code = parse_materiality(raw)
    if stage1 is None:
        return None, code, sha

    if not stage1["is_material"]:
        return (
            {
                "is_material": False,
                "category": "",
                "confidence": stage1["confidence"],
            },
            "",
            sha,
        )

    raw = generator.generate(
        build_category_prompt(text), temperature=0.0, seed=42
    )
    stage2, code = parse_category(raw)
    if stage2 is None:
        # Materiality is known and worth keeping even when the
        # category call fails: a half answer beats discarding both.
        return (
            {
                "is_material": True,
                "category": "",
                "confidence": stage1["confidence"],
            },
            code,
            sha,
        )

    return (
        {
            "is_material": True,
            "category": stage2["category"],
            "confidence": stage2["confidence"],
        },
        "",
        sha,
    )


def extract_claims(summary, *, generator):
    raw = generator.generate(
        build_claim_prompt(summary),
        temperature=0.0,
        seed=42,
    )
    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    claims = data.get("claims") if isinstance(data, dict) else None
    return claims if isinstance(claims, list) else None


def audit_summary(filing, summary, *, generator):
    """
    Returns ({"total", "grounded", "ungrounded"}, failure_code).

    A summary with no checkable claims is not a failure - it scores
    zero of zero, and the caller records it without counting it toward
    any rate.
    """
    if not (summary or "").strip():
        return None, NO_SUMMARY

    built = build_audit_input(filing)
    if built.is_empty:
        return None, NO_FILING_TEXT

    claims = extract_claims(summary, generator=generator)
    if claims is None:
        return None, UNPARSEABLE_RESPONSE

    return check_claims(claims, built.text, summary=summary), ""
