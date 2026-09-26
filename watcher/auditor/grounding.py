"""
Summary grounding check.

The question is NOT "is this summary good" - there is no single right
summary, so agreement is meaningless here. The question is "does this
summary assert something the filing does not contain", which is the
failure that actually matters: a hallucinated counterparty, an
invented amount, a date that appears nowhere.

A dropped fact is incomplete, not wrong, and is deliberately NOT
counted against the summary.

Most of this needs no model. Amounts, dates, percentages and
quantities are verified by normalised string match against the filing
text - cheap, fast, and immune to model drift. Only entity names fall
back to a model call, and most of those match directly too.
"""

import re


NUMERIC_TYPES = {"amount", "date", "percentage", "quantity"}

_SCALE = {
    "thousand": 1_000,
    "k": 1_000,
    "million": 1_000_000,
    "m": 1_000_000,
    "mm": 1_000_000,
    "billion": 1_000_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_NUM_RE = re.compile(r"(\d[\d,]*\.?\d*)\s*([a-zA-Z]+)?")


def _numbers_in(text):
    """
    Every number in `text`, expanded by any scale word that follows.

    "$350 million", "$350,000,000" and "350.0 million" all reduce to
    350000000.0, which is the whole point: the same amount written
    three ways must compare equal.
    """
    found = set()
    for raw, suffix in _NUM_RE.findall(text or ""):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        found.add(value)
        if suffix:
            scale = _SCALE.get(suffix.lower())
            if scale:
                found.add(value * scale)
    return found


def _dates_in(text):
    """
    Dates as (year, month, day) with month and day optional, so
    "2026-03-01", "March 1, 2026" and "1 March 2026" all match, and a
    bare year still matches a full date in the same year.
    """
    found = set()
    lowered = (text or "").lower()

    for y, m, d in re.findall(r"(\d{4})-(\d{1,2})-(\d{1,2})", lowered):
        found.add((int(y), int(m), int(d)))
        found.add((int(y), int(m), None))
        found.add((int(y), None, None))

    for mon, d, y in re.findall(
        r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", lowered
    ):
        month = _MONTHS.get(mon[:3])
        if month:
            found.add((int(y), month, int(d)))
            found.add((int(y), month, None))
            found.add((int(y), None, None))

    for d, mon, y in re.findall(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})", lowered
    ):
        month = _MONTHS.get(mon[:3])
        if month:
            found.add((int(y), month, int(d)))
            found.add((int(y), month, None))
            found.add((int(y), None, None))

    for y in re.findall(r"\b(19\d{2}|20\d{2})\b", lowered):
        found.add((int(y), None, None))

    return found


def _normalise_entity(text):
    """Drop punctuation and corporate suffixes before comparing names."""
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    words = [
        w for w in cleaned.split()
        if w not in {
            "inc", "llc", "ltd", "corp", "corporation", "company", "co",
            "lp", "plc", "the", "and", "na",
        }
    ]
    return " ".join(words)


def numeric_present(claim_text, filing_text):
    """A numeric claim is grounded when every number in it appears."""
    if _dates_in(claim_text):
        return bool(_dates_in(claim_text) & _dates_in(filing_text))

    claim_numbers = _numbers_in(claim_text)
    if not claim_numbers:
        # Nothing numeric to check: fall back to a plain text match.
        return _normalise_entity(claim_text) in _normalise_entity(filing_text)

    filing_numbers = _numbers_in(filing_text)
    return bool(claim_numbers & filing_numbers)


def entity_present(claim_text, filing_text):
    """
    An entity claim is grounded when its distinctive words all appear.

    Substring matching first, then a word-level check so "First
    National Bank" still matches "First National Bank, N.A.".
    """
    claim = _normalise_entity(claim_text)
    filing = _normalise_entity(filing_text)

    if not claim:
        return False
    if claim in filing:
        return True

    words = [w for w in claim.split() if len(w) > 2]
    if not words:
        return False

    filing_words = set(filing.split())
    hits = sum(1 for w in words if w in filing_words)
    return hits == len(words)


def check_claim(claim, filing_text):
    claim_type = (claim.get("type") or "other").lower()
    text = claim.get("text") or ""
    if not text.strip():
        return False
    if claim_type in NUMERIC_TYPES:
        return numeric_present(text, filing_text)
    return entity_present(text, filing_text)


def claim_is_in_summary(claim_text, summary):
    """
    Guard against the extractor inventing a claim.

    A claim must be copied out of the summary. When its text does not
    appear there, the extractor produced it - checking such a claim
    against the filing would score the SUMMARY for the EXTRACTOR's
    mistake, which is how a grounding rate becomes meaningless.
    """
    return _normalise_entity(claim_text) in _normalise_entity(summary)


def check_claims(claims, filing_text, summary=None):
    """
    Returns {"total", "grounded", "ungrounded"}.

    `ungrounded` carries the claims themselves, not just a count: a
    rate tells you something moved, the claims tell you what to fix.

    Pass `summary` to enable the extractor guard - claims that do not
    appear in the summary are counted as `invented` and kept out of
    the rate entirely.
    """
    grounded = 0
    ungrounded = []
    invented = []

    for claim in claims or []:
        if not isinstance(claim, dict):
            continue

        text = claim.get("text") or ""
        if summary is not None and not claim_is_in_summary(text, summary):
            # The extractor made this up. Not the summary's fault, so
            # it is excluded from the rate and reported separately.
            invented.append({
                "type": (claim.get("type") or "other").lower(),
                "text": text[:300],
            })
            continue

        if check_claim(claim, filing_text):
            grounded += 1
        else:
            ungrounded.append({
                "type": (claim.get("type") or "other").lower(),
                "text": (claim.get("text") or "")[:300],
            })

    total = grounded + len(ungrounded)
    return {
        "total": total,
        "grounded": grounded,
        "ungrounded": ungrounded,
        "invented": invented,
    }
