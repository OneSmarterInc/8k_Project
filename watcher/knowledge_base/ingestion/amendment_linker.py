from dataclasses import dataclass

from watcher.knowledge_base.models import Filing


@dataclass
class LinkResult:
    linked: bool
    reason: str = ""


def link_amendment(amendment: Filing) -> LinkResult:
    """
    Attach an 8-K/A amendment to its original 8-K.

    Never guesses when multiple possible originals exist.
    Safe to call repeatedly.
    """

    # This helper only handles 8-K/A filings.
    if amendment.form != "8-K/A":
        return LinkResult(
            linked=False,
            reason="NOT_AN_AMENDMENT",
        )

    # Already linked: nothing else to do.
    if amendment.amends_id is not None:
        return LinkResult(
            linked=True,
        )

    # report_date is required for the current matching rule.
    if not amendment.report_date:
        return LinkResult(
            linked=False,
            reason="NO_REPORT_DATE",
        )

    # Fetch at most two candidates.
    # We only need to know:
    #   0 = no original
    #   1 = safe match
    #   2 = ambiguous
    candidates = list(
        Filing.objects.filter(
            company=amendment.company,
            form="8-K",
            report_date=amendment.report_date,
        )
        .order_by(
            "-filing_date",
            "-accepted_at",
        )[:2]
    )

    # No matching original exists yet.
    if not candidates:
        return LinkResult(
            linked=False,
            reason="NO_ORIGINAL",
        )

    # More than one possible original:
    # DO NOT GUESS.
    if len(candidates) > 1:
        amendment.flag = True
        amendment.flag_reason = (
            "AMBIGUOUS_AMENDMENT_TARGET"
        )

        amendment.save(
            update_fields=[
                "flag",
                "flag_reason",
                "updated_at",
            ]
        )

        return LinkResult(
            linked=False,
            reason="AMBIGUOUS_AMENDMENT_TARGET",
        )

    # Exactly one candidate = safe to link.
    amendment.amends = candidates[0]

    amendment.save(
        update_fields=[
            "amends",
            "updated_at",
        ]
    )

    return LinkResult(
        linked=True,
    )