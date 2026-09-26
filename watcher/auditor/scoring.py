"""
Scoring and alarms.

FIREWALL - READ BEFORE EDITING
==============================
This module must never import, query or otherwise read:

    - price or return data
    - event-study results
    - anything the Analyst produces

The Auditor's only measures are agreement with an independent
classification and grounding against the filing text. A system that
tuned itself toward whatever produced stronger drift would stop being
a classifier and become a curve fitted to the past - it would look
like it worked right up until it was traded on.

test_auditor.py enforces this by reading this module's source. If you
need a variable named "price" here, something upstream is wrong.
"""

import logging

from django.utils import timezone

from watcher.interpreter.taxonomy import TAXONOMY_VERSION, category_codes
from watcher.models import AuditWindow


logger = logging.getLogger(__name__)

PER_CATEGORY_BAR = 0.70
OVERALL_BAR = 0.80
GROUNDING_BAR = 0.90

# Three wrong out of four is 25% and means nothing. Alarming on a
# sample that small teaches everyone to ignore alarms, which is worse
# than having none.
MIN_SAMPLE_FOR_ALARM = 20

CONFIDENCE_BANDS = (
    ("high", 0.9, 1.01),
    ("mid", 0.7, 0.9),
    ("low", 0.0, 0.7),
)


def _rate(hits, total):
    return (hits / total) if total else None


def summarise(samples):
    """
    Per-category and overall agreement, plus grounding, from a list of
    AuditSample rows.
    """
    buckets = {}
    for sample in samples:
        key = sample.classification.category or ""
        buckets.setdefault(key, []).append(sample)

    rows = []
    for category in list(category_codes()) + [""]:
        group = buckets.get(category, [])
        scored = [s for s in group if s.category_agreed is not None]
        if not group:
            continue

        material = [s for s in group if s.material_agreed is not None]
        grounded_rows = [s for s in group if s.summary_claims_total]

        rows.append({
            "category": category,
            "sample_size": len(scored),
            "agreement_rate": _rate(
                sum(1 for s in scored if s.category_agreed), len(scored)
            ),
            "material_agreement_rate": _rate(
                sum(1 for s in material if s.material_agreed), len(material)
            ),
            "grounding_rate": _rate(
                sum(s.summary_claims_grounded for s in grounded_rows),
                sum(s.summary_claims_total for s in grounded_rows),
            ),
        })

    scored_all = [s for s in samples if s.category_agreed is not None]
    material_all = [s for s in samples if s.material_agreed is not None]
    grounded_all = [s for s in samples if s.summary_claims_total]

    overall = {
        "category": "",
        "sample_size": len(scored_all),
        "agreement_rate": _rate(
            sum(1 for s in scored_all if s.category_agreed), len(scored_all)
        ),
        "material_agreement_rate": _rate(
            sum(1 for s in material_all if s.material_agreed), len(material_all)
        ),
        "grounding_rate": _rate(
            sum(s.summary_claims_grounded for s in grounded_all),
            sum(s.summary_claims_total for s in grounded_all),
        ),
    }

    per_category = [r for r in rows if r["category"]]
    return per_category, overall


def confusion(samples):
    """
    predicted -> actual counts for the disagreements.

    "LEGAL_REGULATORY -> OTHER_MATERIAL x7" names the failing
    boundary, which is actionable. "Agreement fell 4 points" is not.
    """
    matrix = {}
    for sample in samples:
        if sample.category_agreed is False:
            key = (
                sample.classification.category or "ROUTINE",
                sample.auditor_category or "ROUTINE",
            )
            matrix[key] = matrix.get(key, 0) + 1
    return dict(sorted(matrix.items(), key=lambda kv: -kv[1]))


def calibration(samples):
    """
    Agreement by the Interpreter's own confidence band.

    Should rise monotonically. When it stops doing so the confidence
    score has become decoration, and the daily review queue is
    routing people to the wrong filings.
    """
    out = []
    for name, low, high in CONFIDENCE_BANDS:
        group = [
            s for s in samples
            if s.category_agreed is not None
            and s.classification.confidence is not None
            and low <= s.classification.confidence < high
        ]
        out.append({
            "band": name,
            "sample_size": len(group),
            "agreement_rate": _rate(
                sum(1 for s in group if s.category_agreed), len(group)
            ),
        })
    return out


_BAND_ORDER = {name: index for index, (name, _, _) in enumerate(CONFIDENCE_BANDS)}


def is_monotonic(bands):
    """
    True when agreement does not fall as confidence rises.

    Order-independent: the caller may hand these over low-to-high or
    high-to-low, and a silently wrong answer here would be worse than
    no check at all.
    """
    ordered = sorted(
        (b for b in bands if b.get("agreement_rate") is not None),
        key=lambda b: _BAND_ORDER.get(b["band"], 0),
        reverse=True,          # low -> high confidence
    )
    rates = [b["agreement_rate"] for b in ordered]
    return all(a <= b for a, b in zip(rates, rates[1:]))


def _breaches(category, rate, sample_size):
    if rate is None or sample_size < MIN_SAMPLE_FOR_ALARM:
        return False
    bar = PER_CATEGORY_BAR if category else OVERALL_BAR
    return rate < bar


def write_windows(run, samples, *, period_start, period_end, is_sealed=False):
    """Persist one AuditWindow per category plus an overall row."""
    per_category, overall = summarise(samples)

    reference = samples[0].classification if samples else None
    prompt_version = reference.prompt_version if reference else ""
    model_name = reference.model_name if reference else ""

    windows = []
    for row in per_category + [overall]:
        window = AuditWindow.objects.create(
            run=run,
            period_start=period_start,
            period_end=period_end,
            taxonomy_version=TAXONOMY_VERSION,
            interpreter_prompt_version=prompt_version,
            interpreter_model_name=model_name,
            category=row["category"],
            sample_size=row["sample_size"],
            agreement_rate=row["agreement_rate"] or 0.0,
            material_agreement_rate=row["material_agreement_rate"],
            grounding_rate=row["grounding_rate"],
            is_sealed=is_sealed,
            below_bar=_breaches(
                row["category"], row["agreement_rate"], row["sample_size"]
            ),
        )
        windows.append(window)
    return windows


def previous_rate(category, *, before, is_sealed=False):
    """Last recorded rate for a category, for the alarm's comparison."""
    row = (
        AuditWindow.objects
        .filter(category=category, is_sealed=is_sealed, period_end__lt=before)
        .order_by("-period_end")
        .first()
    )
    return row.agreement_rate if row else None


def alarm_messages(windows):
    """
    One message per breach, carrying everything needed to act:
    category, window, sample size, previous value, and the versions.
    """
    messages = []
    for window in windows:
        if not window.below_bar:
            continue

        label = window.category or "OVERALL"
        bar = PER_CATEGORY_BAR if window.category else OVERALL_BAR
        prior = previous_rate(
            window.category,
            before=window.period_end,
            is_sealed=window.is_sealed,
        )
        prior_text = f"{prior:.2f}" if prior is not None else "no prior window"

        messages.append(
            f"{label} agreement {window.agreement_rate:.2f} is below the "
            f"{bar:.2f} bar (n={window.sample_size}, "
            f"{window.period_start} to {window.period_end}, "
            f"previous {prior_text}, taxonomy {window.taxonomy_version}, "
            f"interpreter prompt {window.interpreter_prompt_version or '?'}, "
            f"model {window.interpreter_model_name or '?'})."
        )

        window.alarmed_at = timezone.now()
        window.save(update_fields=["alarmed_at"])

    return messages
