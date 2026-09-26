"""
Interpreter prompt, version 1.0.1.

Change from 1.0.0 (found in the first real run, 2026-09-26):
    Every ROUTINE answer came back with confidence 0.00, so every routine
    filing was sent to human review. The model read "confidence" as
    confidence in the CATEGORY, and a routine filing has no category.

Fix:
    1. Define confidence as confidence in the WHOLE answer, including
       the material/routine decision.
    2. Show a routine example with a real confidence, next to the
       material one, so the model sees both shapes.

Everything else is unchanged from 1.0.0. 1.0.0 is kept, not edited, so
its rows stay reproducible (guide 5.2 / 5.5).
"""

from watcher.interpreter.taxonomy import CATEGORIES, MATERIAL_DEFINITION

PROMPT_VERSION = "1.0.1"


def category_block():
    # Self-contained on purpose: this version must load even if an older
    # prompt file is missing.
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

Step 1: decide material or routine.
"""

_SYSTEM_MID = """
Step 2: if the filing is routine, set "is_material" to false and
"category" to null. If it is material, choose exactly one category:
"""

_SYSTEM_TAIL = """

Confidence: a number from 0.0 to 1.0 for how sure you are that your
WHOLE answer is right - the material/routine decision AND, when
material, the category.

A routine answer still needs a real confidence. If a filing is clearly
routine (for example only a Reg FD investor-presentation cover note, or
vote results for uncontested routine proposals), that is a confident
answer: give a high score such as 0.9. Never use 0.0 just because the
category is null.

Give a low score only when you are genuinely unsure: the filing is
ambiguous, it could reasonably belong to two categories, it is unclear
whether it is material, or the body is too thin to tell. Do not inflate
the score, and do not deflate it.

Respond with JSON only, in exactly this shape.

Example of a material answer:
{
  "is_material": true,
  "category": "FINANCING",
  "confidence": 0.85,
  "body_item_numbers": ["1.01", "9.01"],
  "extracted_facts": {
    "counterparty": "First National Bank",
    "amount_usd": 350000000,
    "effective_date": "2026-09-14"
  },
  "reasoning": "one sentence"
}

Example of a routine answer:
{
  "is_material": false,
  "category": null,
  "confidence": 0.9,
  "body_item_numbers": ["7.01", "9.01"],
  "extracted_facts": {
    "counterparty": null,
    "amount_usd": null,
    "effective_date": null
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
