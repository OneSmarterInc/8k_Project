"""
Interpreter prompt, version 1.0.2.

Change from 1.0.1 (found 2026-09-26 on AIG 0001104659-26-110438):
    The model's reasoning named "EUR 625 million" and "EUR 500 million",
    but extracted_facts came back all null. 1.0.1 asked only for
    "amount_usd", so a euro amount had nowhere to go, and the rule
    "never infer" correctly stopped it converting currencies.

Fix:
    1. "amounts" is a list of {value, currency, description}, in the
       filing's own currency, one entry per tranche.
    2. "event_date" (the date the event happened or takes effect) replaces
       "effective_date", with an explicit instruction to convert a
       written date such as "September 24, 2026".
    3. "counterparty" is defined: the other named party, or null.
    4. Both examples show filled facts, so the model sees what is wanted.

Everything else is unchanged from 1.0.1. Older versions are kept.
Self-contained: loads even if older prompt files are missing.
"""

from watcher.interpreter.taxonomy import CATEGORIES, MATERIAL_DEFINITION

PROMPT_VERSION = "1.0.2"


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

Step 1: decide material or routine.
"""

_SYSTEM_MID = """
Step 2: if the filing is routine, set "is_material" to false and
"category" to null. If it is material, choose exactly one category:
"""

_SYSTEM_TAIL = """

Step 3: extract facts that are WRITTEN in the filing.

- counterparty: the other named party to the agreement or transaction
  (lender, borrower, buyer, seller, customer, supplier, underwriter,
  regulator). Use the name as written. null if no other party is named.
- amounts: every money amount that describes the event, one entry per
  amount, in the filing's OWN currency. Write the full number:
  "EUR 625 million" -> {"value": 625000000, "currency": "EUR"}.
  "$1.2 billion" -> {"value": 1200000000, "currency": "USD"}.
  Use the ISO code (USD, EUR, GBP, JPY, CAD...). Never convert between
  currencies. Add a short description when there are several, such as
  "4.250% Notes due 2031". Use [] if no amount is written.
- event_date: the date the event happened or takes effect (closing,
  signing, completion, effective date), converted to YYYY-MM-DD:
  "on September 24, 2026" -> "2026-09-24". null if no date is written.

Never infer a fact that is not written down.

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
  "body_item_numbers": ["8.01", "9.01"],
  "extracted_facts": {
    "counterparty": "Goldman Sachs & Co. LLC",
    "amounts": [
      {"value": 625000000, "currency": "EUR", "description": "4.250% Notes due 2031"},
      {"value": 500000000, "currency": "EUR", "description": "4.750% Notes due 2036"}
    ],
    "event_date": "2026-09-24"
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
    "amounts": [],
    "event_date": null
  },
  "reasoning": "one sentence"
}"""


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
