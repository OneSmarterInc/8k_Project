"""
Two sampling streams, answering different questions.

FRESH   newly classified filings, stratified across categories and
        weighted toward low confidence. Catches distribution shift -
        the filings themselves changing under a fixed model.

SEALED  the GroundTruthSplit.SEALED slice, re-scored every run.
        Catches the one failure agreement cannot see: two models
        drifting the SAME direction after a model upgrade, which
        leaves the agreement rate untouched while real accuracy
        falls. Human answers written once cannot drift, so they are
        a fixed reference point.

The sealed stream is optional. With no sealed rows the Auditor still
works - it just goes quiet on model upgrades, which is when you would
most want it talking.
"""

from watcher.interpreter.taxonomy import category_codes
from watcher.models import AuditSample, FilingClassification, GroundTruthSplit


def _latest_per_filing(queryset):
    """
    One classification per filing - the newest. A filing reclassified
    under a new taxonomy or prompt version has several, and auditing
    a superseded row would measure a version nobody runs any more.
    """
    seen = set()
    picked = []
    for row in queryset.order_by("filing_id", "-created_at"):
        if row.filing_id in seen:
            continue
        seen.add(row.filing_id)
        picked.append(row)
    return picked


def fresh_sample(size=100, *, min_per_category=1):
    """
    Stratified across categories, least-confident first.

    NOT a uniform draw. A uniform sample spends the whole budget on
    the two largest categories and never accumulates enough of the
    thin ones to say anything about them - and per-category tracking
    is the entire point of the Auditor.
    """
    codes = category_codes()
    per_category = max(min_per_category, size // max(1, len(codes)))

    picked = []
    for code in codes:
        rows = _latest_per_filing(
            FilingClassification.objects
            .filter(category=code, audit_samples__isnull=True)
            .select_related("filing", "filing__company")
        )
        rows.sort(key=lambda r: (r.confidence is None, r.confidence or 0.0))
        picked += rows[:per_category]

    if len(picked) < size:
        # Quotas rarely fill the sample exactly: a thin category may
        # have fewer rows than its share, and routine filings have no
        # category at all. Top up from everything still unaudited,
        # least-confident first, so the sample size is honoured and
        # material-vs-routine stays measurable.
        chosen = {row.id for row in picked}
        remainder = _latest_per_filing(
            FilingClassification.objects
            .filter(audit_samples__isnull=True)
            .exclude(id__in=chosen)
            .select_related("filing", "filing__company")
        )
        remainder.sort(key=lambda r: (r.confidence is None, r.confidence or 0.0))

        for row in remainder:
            if len(picked) >= size:
                break
            picked.append(row)

    return picked[:size]


def sealed_sample(size=50):
    """The sealed ground-truth slice, re-scored every run."""
    return _latest_per_filing(
        FilingClassification.objects
        .filter(filing__ground_truth_split__split=GroundTruthSplit.Split.SEALED)
        .select_related("filing", "filing__company")
    )[:size]


def build_audit_sample(size=100, sealed_size=50):
    return fresh_sample(size), sealed_sample(sealed_size)
