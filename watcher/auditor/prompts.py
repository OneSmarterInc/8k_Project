"""
Auditor prompts, versioned like the Interpreter's.

AUDIT_PROMPT_VERSION is stamped on every AuditorRun. When agreement
moves, the first question is whether the Auditor changed, the
Interpreter changed, or the filings changed - and that is only
answerable if both sides are versioned.

The wording here is deliberately NOT a copy of the Interpreter's
prompt. Two identical prompts on the same model at temperature 0
return the same answer, which would make the agreement rate a
tautology rather than a measurement.
"""

from watcher.interpreter.taxonomy import CATEGORIES, MATERIAL_DEFINITION


AUDIT_PROMPT_VERSION = "2.0.0"


def _category_block():
    lines = []
    for code, spec in CATEGORIES.items():
        lines.append(f"{code} - {spec['label']}: {spec['definition']}")
        if spec.get("excludes"):
            lines.append(f"    not: {'; '.join(spec['excludes'])}")
    return "\n".join(lines)


def build_materiality_prompt(filing_text):
    """
    STAGE 1 of 2. Materiality only.

    The category list is deliberately ABSENT from this prompt. An
    earlier single-prompt version listed the five categories first and
    then asked about materiality; handed a routine Item 7.01 press
    release, the model reached for the nearest category instead of
    answering "routine". Every disagreement in the first real run
    landed in one bucket for exactly this reason.

    A model cannot be primed by a list it has not been shown.
    """
    return f"""You are reading an SEC 8-K filing and making ONE
decision: does it report a substantive event, or is it routine?

{MATERIAL_DEFINITION}

Routine filings are common and "routine" is the correct answer for
them. Do not look for significance that is not there.

Strong signals of a routine filing:
  - Item 7.01 (Regulation FD) covering a press release or investor deck
  - Item 9.01 alone (exhibits only)
  - Item 5.07 vote tallies for uncontested routine proposals
  - the body only says a release is furnished, with the substance in
    an exhibit and no new agreement or event described

Judge only what the filing says. Do not consider whether any news is
good or bad and do not predict a market reaction.

Respond with this JSON shape and nothing else. The angle brackets
describe what goes in each field and must never be copied:

{{"is_material": <true or false>,
  "confidence": <number between 0.0 and 1.0>,
  "reason": "<one short sentence>"}}

FILING:
{filing_text}
"""


def build_category_prompt(filing_text):
    """
    STAGE 2 of 2. Runs ONLY after stage 1 answered material.

    By the time the categories appear, the materiality decision is
    already made and recorded, so the list cannot influence it.
    """
    return f"""This SEC 8-K filing reports a substantive event. Decide
which kind.

Pick exactly one category:
{_category_block()}

Judge only what the filing says. Do not consider whether the news is
good or bad and do not predict a market reaction.

Give a confidence between 0.0 and 1.0. Score low when the filing could
reasonably belong to two categories or when the text is too thin to
tell. A low score is the correct answer for a genuinely unclear
filing.

Respond with this JSON shape and nothing else. The angle brackets
describe what goes in each field and must never be copied:

{{"category": "<one category code from the list above>",
  "confidence": <number between 0.0 and 1.0>}}

FILING:
{filing_text}
"""


CLAIM_PROMPT_VERSION = "1.1.0"


def build_claim_prompt(summary):
    """
    Stage 1 of the grounding check: pull checkable claims out of the
    summary. Only specific assertions - a characterisation like
    "significant expansion" is not checkable and must not be extracted.
    """
    return f"""Extract every specific factual claim from the summary
below.

A claim is a concrete assertion that could be checked against the
source document: a party name, a money amount, a date, a percentage,
or a quantity. Opinions, characterisations and general statements are
NOT claims - skip them.

Every "text" value MUST be copied verbatim from the summary below.
Do not invent, complete or substitute a value. If the summary does not
state an amount, there is no amount claim.

Respond with this JSON shape and nothing else. The angle brackets
describe what goes in each field - they are not example values and
must never be copied:

{{"claims": [
  {{"type": "<one of: amount, date, percentage, quantity, counterparty, other>",
    "text": "<the exact words from the summary>"}}
]}}

If the summary contains no checkable claims, return {{"claims": []}}.

SUMMARY:
{summary}
"""
