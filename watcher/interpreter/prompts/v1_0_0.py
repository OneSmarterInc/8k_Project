"""
Guide 5.5: the Interpreter prompt, version 1.0.0.

Three things do real work here:
1. Telling the model NOT to judge good/bad news or market reaction stops
   it drifting into sentiment.
2. Legitimising low confidence makes the score informative.
3. Forbidding inferred facts keeps the extraction usable.

The category block is built from taxonomy.py, so the prompt cannot drift
from the definitions the labellers used.

Built with string concatenation, not str.format: the JSON example
contains braces.
"""

from watcher.interpreter.taxonomy import CATEGORIES, MATERIAL_DEFINITION

PROMPT_VERSION = "1.0.0"


def category_block():
    lines = []
    for code, spec in CATEGORIES.items():
        lines.append(f"- {code}: {spec['definition']}")
        if spec["includes"]:
            lines.append("    includes: " + "; ".join(spec["includes"]))
        if spec["excludes"]:
            lines.append("    excludes: " + "; ".join(spec["excludes"]))
    return "\n".join(lines)


_SYSTEM_HEAD = """You classify SEC 8-K filings. You answer only with JSON.

You are not deciding whether the news is good or bad, and not
predicting any market reaction. You are deciding what kind of event
the filing describes.

First decide material or routine.
"""

_SYSTEM_MID = """
If the filing is routine, set "is_material" to false and "category" to null.
If it is material, choose exactly one category:
"""

_SYSTEM_TAIL = """

Confidence: 0.0 to 1.0. Give a low score when the filing is
ambiguous, when it could reasonably belong to two categories, or when
the body is too thin to tell. A low score is a correct answer when
the filing is genuinely unclear. Do not inflate it.

Respond with this JSON and nothing else:
{
  "is_material": true,
  "category": "FINANCING",
  "confidence": 0.85,
  "body_item_numbers": ["1.01", "9.01"],
  "extracted_facts": {
    "counterparty": "...",
    "amount_usd": 0,
    "effective_date": "YYYY-MM-DD"
  },
  "reasoning": "one sentence"
}

Use null for any fact not stated in the filing. Never infer an
amount, a date or a counterparty that is not written down."""


def system_prompt():
    return (
        _SYSTEM_HEAD
        + MATERIAL_DEFINITION
        + "\n"
        + _SYSTEM_MID
        + category_block()
        + _SYSTEM_TAIL
    )


def build_prompt(filing, filing_text, *, truncated=False):
    header = (
        f"Form: {filing.form}\n"
        f"Company: {filing.company.name} ({filing.company.ticker})\n"
        f"Cover-page item numbers: {filing.sec_item_codes or 'none listed'}\n"
    )
    if truncated:
        header += "Note: the filing text below was truncated.\n"

    return (
        system_prompt()
        + "\n\n=== FILING ===\n"
        + header
        + "\n"
        + filing_text
        + "\n=== END FILING ===\n\nJSON:"
    )
