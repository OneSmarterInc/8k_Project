"""
Auditor reporting endpoints.

GET /api/audit/summary/?days=90   trends, latest window, calibration
GET /api/audit/disagreements/     the filings the two reads differ on

Read-only. The Auditor writes through `manage.py audit`; nothing here
creates or edits a row, because an audit result edited by hand
corrupts the trend it belongs to.
"""

import datetime

from django.db.models import Sum
from django.utils import timezone

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from watcher.auditor import scoring
from watcher.interpreter.taxonomy import category_codes
from watcher.models import AuditorRun, AuditSample, AuditWindow


def _window_payload(window):
    return {
        "category": window.category or "OVERALL",
        "period_start": window.period_start,
        "period_end": window.period_end,
        "sample_size": window.sample_size,
        "agreement_rate": window.agreement_rate,
        "material_agreement_rate": window.material_agreement_rate,
        "grounding_rate": window.grounding_rate,
        "below_bar": window.below_bar,
        "is_sealed": window.is_sealed,
        "alarmed_at": window.alarmed_at,
        # Version context travels with every number. When a rate
        # moves, the first question is whether the system changed or
        # the filings did, and that is unanswerable without these.
        "taxonomy_version": window.taxonomy_version,
        "interpreter_prompt_version": window.interpreter_prompt_version,
        "interpreter_model_name": window.interpreter_model_name,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_summary(request):
    try:
        days = int(request.GET.get("days", 90))
    except (TypeError, ValueError):
        days = 90
    days = max(1, min(days, 365))

    since = timezone.localdate() - datetime.timedelta(days=days)

    windows = list(
        AuditWindow.objects
        .filter(period_end__gte=since)
        .order_by("period_end", "category")
    )

    latest_run = AuditorRun.objects.order_by("-started_at").first()

    latest = {}
    if latest_run:
        latest = {
            w.category or "OVERALL": _window_payload(w)
            for w in windows
            if w.run_id == latest_run.id and not w.is_sealed
        }

    # Trend: one series per category, oldest first, so the chart can
    # plot it without re-sorting.
    trend = {}
    for window in windows:
        if window.is_sealed:
            continue
        trend.setdefault(window.category or "OVERALL", []).append({
            "period_end": window.period_end,
            "agreement_rate": window.agreement_rate,
            "sample_size": window.sample_size,
            "grounding_rate": window.grounding_rate,
            "below_bar": window.below_bar,
        })

    sealed_trend = [
        {
            "period_end": w.period_end,
            "agreement_rate": w.agreement_rate,
            "sample_size": w.sample_size,
        }
        for w in windows
        if w.is_sealed and not w.category
    ]

    samples = []
    if latest_run:
        samples = list(
            AuditSample.objects
            .filter(run=latest_run)
            .select_related("classification")
        )

    grounding = AuditSample.objects.filter(
        run=latest_run
    ).aggregate(
        total=Sum("summary_claims_total"),
        grounded=Sum("summary_claims_grounded"),
    ) if latest_run else {"total": 0, "grounded": 0}

    calibration = scoring.calibration(samples) if samples else []

    return Response({
        "bars": {
            "per_category": scoring.PER_CATEGORY_BAR,
            "overall": scoring.OVERALL_BAR,
            "grounding": scoring.GROUNDING_BAR,
            "min_sample_for_alarm": scoring.MIN_SAMPLE_FOR_ALARM,
        },
        "categories": list(category_codes()),
        "latest_run": {
            "id": latest_run.id,
            "started_at": latest_run.started_at,
            "completed_at": latest_run.completed_at,
            "status": latest_run.status,
            "sampled_count": latest_run.sampled_count,
            "classification_checked": latest_run.classification_checked,
            "summary_checked": latest_run.summary_checked,
            "error_count": latest_run.error_count,
            "taxonomy_version": latest_run.taxonomy_version,
            "auditor_prompt_version": latest_run.auditor_prompt_version,
            "auditor_model_name": latest_run.auditor_model_name,
        } if latest_run else None,
        "latest": latest,
        "trend": trend,
        "sealed_trend": sealed_trend,
        "calibration": calibration,
        "calibration_monotonic": (
            scoring.is_monotonic(calibration) if calibration else None
        ),
        "confusion": [
            {"predicted": p, "actual": a, "count": n}
            for (p, a), n in scoring.confusion(samples).items()
        ],
        "grounding": {
            "total_claims": grounding.get("total") or 0,
            "grounded_claims": grounding.get("grounded") or 0,
            "rate": (
                (grounding.get("grounded") or 0) / grounding["total"]
                if grounding.get("total") else None
            ),
        },
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_disagreements(request):
    """
    The filings the two reads differ on, newest first.

    This is the actionable half of the report: a rate says something
    moved, these say what to read.
    """
    queryset = (
        AuditSample.objects
        .filter(category_agreed=False)
        .select_related(
            "classification",
            "classification__filing",
            "classification__filing__company",
        )
        .order_by("-created_at")
    )

    category = request.GET.get("category")
    if category:
        queryset = queryset.filter(classification__category=category)

    rows = []
    for sample in queryset[:200]:
        filing = sample.classification.filing
        rows.append({
            "id": sample.id,
            "ticker": filing.company.ticker,
            "accession": filing.accession_number,
            "filing_date": filing.filing_date,
            "interpreter_category": sample.classification.category or "ROUTINE",
            "interpreter_confidence": sample.classification.confidence,
            "auditor_category": sample.auditor_category or "ROUTINE",
            "auditor_confidence": sample.auditor_confidence,
            "material_agreed": sample.material_agreed,
            "ungrounded_claims": sample.ungrounded_claims,
            "is_sealed": sample.is_sealed,
            "created_at": sample.created_at,
        })

    return Response({"count": len(rows), "results": rows})
