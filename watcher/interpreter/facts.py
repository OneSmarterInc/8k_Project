"""
Extracted facts: cleaning (parser side) and display text (email side).

Prompt 1.0.2 asks for:
    counterparty  str | null
    amounts       [{"value": 625000000, "currency": "EUR", "description": "..."}]
    event_date    "YYYY-MM-DD" | null

Rows written by prompts 1.0.0 / 1.0.1 hold amount_usd / effective_date.
Both shapes are kept and both display, so old rows never break.
"""

import math
import re

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
MAX_AMOUNTS = 10


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def _text(value, limit):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def _date(value):
    value = _text(value, 10)
    return value if value and _DATE_RE.match(value) else None


def clean_facts(value):
    """Keep what the model wrote, drop anything malformed. Never invents."""
    if not isinstance(value, dict):
        return {}

    facts = {str(k): v for k, v in value.items()}

    if "counterparty" in facts:
        facts["counterparty"] = _text(facts["counterparty"], 300)

    if "event_date" in facts:
        facts["event_date"] = _date(facts["event_date"])

    if "amounts" in facts:
        cleaned = []
        raw = facts["amounts"] if isinstance(facts["amounts"], list) else []
        for item in raw[:MAX_AMOUNTS]:
            if not isinstance(item, dict):
                continue
            number = _number(item.get("value"))
            if number is None:
                continue
            # Validate the whole value; never truncate "EURO" into "EUR".
            currency = _text(item.get("currency"), 20)
            currency = currency.upper() if currency else None
            if currency and not _CURRENCY_RE.match(currency):
                currency = None
            cleaned.append({
                "value": number,
                "currency": currency,
                "description": _text(item.get("description"), 200),
            })
        facts["amounts"] = cleaned

    return facts


def format_amounts(facts):
    """'EUR 625,000,000 (2031 Notes); EUR 500,000,000' or None."""
    facts = facts or {}
    amounts = facts.get("amounts")

    if isinstance(amounts, list) and amounts:
        parts = []
        for item in amounts:
            value = _number(item.get("value")) if isinstance(item, dict) else None
            if value is None:
                continue
            currency = item.get("currency") or ""
            text = f"{currency} {value:,.0f}".strip()
            if item.get("description"):
                text += f" ({item['description']})"
            parts.append(text)
        return "; ".join(parts) or None

    legacy = _number(facts.get("amount_usd"))
    if legacy is not None:
        return f"${legacy:,.0f}"

    return None


def event_date(facts):
    facts = facts or {}
    return facts.get("event_date") or facts.get("effective_date") or None
