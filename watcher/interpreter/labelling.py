"""
Guide 4.2: the blind, stratified labelling queue.

Rules enforced here, not just in the UI:

1. A labeller never sees another labeller's answer. The queue excludes
   filings the caller has labelled, and nothing returned by this module
   contains any other label.
2. A labeller never sees model output. Callers serialize filing text
   from filing_text.build_filing_text() only - never summary, flag or
   any classification.
3. Labellers do not choose. Samples are drawn by build_labelling_sample
   and served in position order.
4. Double-labelled filings (required_labels = 2) feed the kappa check.
5. A correction is a new row with `supersedes`; nothing is overwritten.
"""

import random
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from watcher.models import (
    Filing,
    FilingChunk,
    GroundTruthLabel,
    LabellingSample,
)
from watcher.services.item_codes import parse_item_codes

from .taxonomy import TAXONOMY_VERSION, is_valid_category


LABELLER_GROUP = "labeller"
LABELLABLE_FORMS = ("8-K", "8-K/A")
CLAIM_TTL = timedelta(minutes=20)

# 9.01 (exhibits) rides along on most 8-Ks, so it is a poor stratum.
_SECONDARY_ITEMS = {"9.01"}


class LabellingError(Exception):
    """Validation failure. `status` is the HTTP status the API returns."""

    def __init__(self, message, *, status=400, field=None):
        super().__init__(message)
        self.status = status
        self.field = field


# ----------------------------------------------------------------------
# Access
# ----------------------------------------------------------------------

def user_can_label(user):
    if not (user and user.is_authenticated and user.is_active):
        return False

    if user.is_staff:
        return True

    return user.groups.filter(name=LABELLER_GROUP).exists()


# ----------------------------------------------------------------------
# Sampling (used by build_labelling_sample)
# ----------------------------------------------------------------------

def primary_stratum(sec_item_codes, parsed_item_codes=""):
    # Cover-page codes first; fall back to the codes the Watcher parsed
    # from the body when the cover-page field is empty.
    codes = parse_item_codes(sec_item_codes) or parse_item_codes(
        parsed_item_codes
    )

    for code in codes:
        if code not in _SECONDARY_ITEMS:
            return code

    return codes[0] if codes else ""


def eligible_filings():
    """8-K / 8-K/A filings that have text to label and no sample yet."""
    with_chunks = FilingChunk.objects.values("filing_id")

    return (
        Filing.objects
        .filter(form__in=LABELLABLE_FORMS, id__in=with_chunks)
        .filter(labelling_sample__isnull=True)
        .only("id", "sec_item_codes", "parsed_item_codes")
    )


def allocate(strata_sizes, size, min_per_stratum):
    """
    Proportional allocation with a floor, so rare item codes are
    represented. Returns {stratum: n}. Never exceeds what exists.
    """
    total = sum(strata_sizes.values())

    if total == 0 or size <= 0:
        return {s: 0 for s in strata_sizes}

    size = min(size, total)

    allocation = {
        s: min(n, min_per_stratum) for s, n in strata_sizes.items()
    }

    remaining = size - sum(allocation.values())

    if remaining < 0:
        # The floors alone exceed the budget: trim the largest strata.
        # First keep at least one per stratum, then (only if there are
        # more strata than the budget) drop the largest to zero.
        for floor in (1, 0):
            for s in sorted(allocation, key=lambda k: -allocation[k]):
                while remaining < 0 and allocation[s] > floor:
                    allocation[s] -= 1
                    remaining += 1
        return allocation

    spare = {s: strata_sizes[s] - allocation[s] for s in strata_sizes}
    spare_total = sum(spare.values())

    if spare_total == 0:
        return allocation

    # Largest-remainder proportional fill of what is left.
    shares = {
        s: remaining * spare[s] / spare_total for s in spare
    }
    for s, share in shares.items():
        allocation[s] += min(int(share), spare[s])

    leftover = size - sum(allocation.values())
    order = sorted(
        shares,
        key=lambda s: shares[s] - int(shares[s]),
        reverse=True,
    )
    for s in order:
        if leftover <= 0:
            break
        if allocation[s] < strata_sizes[s]:
            allocation[s] += 1
            leftover -= 1

    return allocation


@transaction.atomic
def build_sample(*, size, double, seed, min_per_stratum=5):
    """
    Draw `size` new filings, `double` of them marked for two labellers.
    Appends after any existing sample; never touches existing rows.
    Returns the list of created LabellingSample rows.
    """
    rng = random.Random(seed)

    by_stratum = defaultdict(list)
    for filing in eligible_filings().order_by("id"):
        by_stratum[
            primary_stratum(filing.sec_item_codes, filing.parsed_item_codes)
        ].append(filing.id)

    allocation = allocate(
        {s: len(ids) for s, ids in by_stratum.items()},
        size,
        min_per_stratum,
    )

    chosen = []
    for stratum in sorted(by_stratum):
        ids = by_stratum[stratum]
        chosen += [
            (fid, stratum)
            for fid in rng.sample(ids, allocation.get(stratum, 0))
        ]

    rng.shuffle(chosen)

    # Keep the double-labelled SHARE, not the count. If fewer filings
    # exist than asked for, 100-of-500 must become 20% of what was drawn,
    # not "every filing twice".
    if chosen and len(chosen) < size:
        double = round(len(chosen) * double / size)
    double = max(0, min(double, len(chosen)))
    double_ids = {fid for fid, _ in rng.sample(chosen, double)}

    start = (
        LabellingSample.objects.order_by("-position")
        .values_list("position", flat=True)
        .first()
    )
    start = 0 if start is None else start + 1

    rows = [
        LabellingSample(
            filing_id=fid,
            stratum=stratum,
            required_labels=2 if fid in double_ids else 1,
            position=start + i,
            taxonomy_version=TAXONOMY_VERSION,
        )
        for i, (fid, stratum) in enumerate(chosen)
    ]

    return LabellingSample.objects.bulk_create(rows)


# ----------------------------------------------------------------------
# Queue
# ----------------------------------------------------------------------

def _active_labels():
    """Labels that have not been corrected by a later row."""
    return GroundTruthLabel.objects.filter(superseded_by__isnull=True)


def _annotated_samples():
    return LabellingSample.objects.annotate(
        labels_done=Count(
            "filing__ground_truth_labels__labeller",
            filter=Q(
                filing__ground_truth_labels__superseded_by__isnull=True
            ),
            distinct=True,
        ),
    ).annotate(
        labels_remaining=F("required_labels") - F("labels_done"),
    )


def next_sample_for(user):
    """
    The next sample this user should label, claimed for them, or None
    when their queue is empty. A sample they already hold a live claim
    on comes back first, so a page refresh does not skip a filing.
    """
    now = timezone.now()
    claim_cutoff = now - CLAIM_TTL

    mine = GroundTruthLabel.objects.filter(labeller=user).values("filing_id")

    candidates = (
        _annotated_samples()
        .filter(labels_remaining__gt=0)
        .exclude(filing_id__in=mine)
        .exclude(
            # Someone else is working on the last open slot.
            Q(labels_remaining=1)
            & Q(claimed_at__gte=claim_cutoff)
            & ~Q(claimed_by=user)
            & Q(claimed_by__isnull=False)
        )
        .order_by("position")
    )

    held = candidates.filter(claimed_by=user, claimed_at__gte=claim_cutoff)
    ordered_ids = list(held.values_list("id", flat=True)[:1]) + list(
        candidates.values_list("id", flat=True)[:25]
    )

    for sample_id in ordered_ids:
        with transaction.atomic():
            sample = (
                LabellingSample.objects
                .select_for_update(skip_locked=True)
                .filter(pk=sample_id)
                .select_related("filing", "filing__company")
                .first()
            )

            if sample is None:
                continue

            # Re-check the claim under the lock.
            claimed_by_other = (
                sample.claimed_by_id not in (None, user.id)
                and sample.claimed_at is not None
                and sample.claimed_at >= claim_cutoff
            )
            remaining = sample.required_labels - (
                _active_labels()
                .filter(filing_id=sample.filing_id)
                .values("labeller").distinct().count()
            )

            if remaining <= 0:
                continue
            if claimed_by_other and remaining == 1:
                continue

            sample.claimed_by = user
            sample.claimed_at = now
            sample.save(update_fields=["claimed_by", "claimed_at"])
            return sample

    return None


def progress_for(user):
    totals = _annotated_samples().aggregate(
        slots=Sum("required_labels"),
    )
    slots = totals["slots"] or 0

    filled = (
        _active_labels()
        .filter(filing__labelling_sample__isnull=False)
        .values("filing_id", "labeller_id")
        .distinct()
        .count()
    )

    mine = (
        _active_labels()
        .filter(labeller=user, filing__labelling_sample__isnull=False)
        .count()
    )

    return {
        "labelled_by_me": mine,
        "slots_total": slots,
        "slots_filled": filled,
        "samples_total": LabellingSample.objects.count(),
    }


# ----------------------------------------------------------------------
# Recording a label
# ----------------------------------------------------------------------

def _clean_payload(data):
    is_material = data.get("is_material")
    if not isinstance(is_material, bool):
        raise LabellingError(
            "is_material must be true or false.", field="is_material"
        )

    category = data.get("category") or ""
    if not isinstance(category, str):
        raise LabellingError("category must be a string.", field="category")
    category = category.strip().upper()

    if is_material:
        if not is_valid_category(category):
            raise LabellingError(
                f'"{category}" is not a valid category.', field="category"
            )
    elif category:
        raise LabellingError(
            "A routine filing has no category.", field="category"
        )

    confidence = data.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int)
        or not 1 <= confidence <= 5
    ):
        raise LabellingError(
            "confidence must be a whole number from 1 to 5.",
            field="confidence",
        )

    notes = data.get("notes") or ""
    if not isinstance(notes, str):
        raise LabellingError("notes must be text.", field="notes")

    duration = data.get("duration_seconds")
    if duration is not None:
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or duration < 0
        ):
            raise LabellingError(
                "duration_seconds must be a non-negative whole number.",
                field="duration_seconds",
            )

    return {
        "is_material": is_material,
        "category": category,
        "confidence": confidence,
        "notes": notes.strip(),
        "duration_seconds": duration,
    }


@transaction.atomic
def record_label(user, data):
    try:
        filing_id = int(data.get("filing_id"))
    except (TypeError, ValueError):
        raise LabellingError("filing_id is required.", field="filing_id")

    sample = (
        LabellingSample.objects
        .select_for_update()
        .filter(filing_id=filing_id)
        .first()
    )
    if sample is None:
        raise LabellingError(
            "This filing is not in the labelling sample.", status=404
        )

    clean = _clean_payload(data)

    supersedes = None
    supersedes_id = data.get("supersedes")

    if supersedes_id is not None:
        supersedes = (
            GroundTruthLabel.objects
            .select_for_update()
            .filter(
                pk=supersedes_id,
                filing_id=filing_id,
                labeller=user,
            )
            .first()
        )
        if supersedes is None:
            raise LabellingError(
                "You can only correct your own label on this filing.",
                status=404,
                field="supersedes",
            )
        if supersedes.superseded_by.exists():
            raise LabellingError(
                "That label has already been corrected.", status=409
            )
    else:
        if _active_labels().filter(
            filing_id=filing_id, labeller=user
        ).exists():
            raise LabellingError(
                "You have already labelled this filing.", status=409
            )

        others = (
            _active_labels()
            .filter(filing_id=filing_id)
            .values("labeller").distinct().count()
        )
        if others >= sample.required_labels:
            raise LabellingError(
                "This filing already has all the labels it needs.",
                status=409,
            )

    label = GroundTruthLabel.objects.create(
        filing_id=filing_id,
        labeller=user,
        taxonomy_version=TAXONOMY_VERSION,
        supersedes=supersedes,
        **clean,
    )

    if sample.claimed_by_id == user.id:
        sample.claimed_by = None
        sample.claimed_at = None
        sample.save(update_fields=["claimed_by", "claimed_at"])

    return label
