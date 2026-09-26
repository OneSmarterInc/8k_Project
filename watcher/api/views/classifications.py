"""
Interpreter review endpoints.

POST /api/classifications/<id>/override/   staff: record a human decision
GET  /api/taxonomy/                        the frozen category list

The override is a NEW row (ClassificationOverride). The model's original
FilingClassification is never edited, so the Auditor can later count how
often the model was wrong. First reviewer wins; a second gets 409.
"""

from django.db import IntegrityError, transaction
from django.db.models import Prefetch

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from watcher.interpreter.taxonomy import (
    TAXONOMY_VERSION,
    is_valid_category,
    taxonomy_payload,
)
from watcher.knowledge_base.models import (
    ClassificationOverride,
    FailureEvent,
    Filing,
    FilingClassification,
)

from ..serializers import FilingSerializer, serialize_classification


def _filing_row(request, filing_id):
    """The filing in exactly the shape /api/filings/ returns."""
    filing = (
        Filing.objects
        .select_related("company", "summary_cache")
        .prefetch_related(
            Prefetch(
                "failure_events",
                queryset=(
                    FailureEvent.objects
                    .filter(resolved_at__isnull=True)
                    .order_by("-created_at")
                ),
                to_attr="prefetched_unresolved_failures",
            ),
            Prefetch(
                "classifications",
                queryset=(
                    FilingClassification.objects
                    .select_related("override", "override__reviewer")
                    .order_by("-created_at", "-id")
                ),
                to_attr="prefetched_classifications",
            ),
        )
        .get(pk=filing_id)
    )
    return FilingSerializer(filing, context={"request": request}).data


def _validate(data):
    is_material = data.get("is_material")
    if not isinstance(is_material, bool):
        return None, {"is_material": ["is_material must be true or false."]}

    category = data.get("category") or ""
    if not isinstance(category, str):
        return None, {"category": ["category must be a string."]}
    category = category.strip().upper()

    if is_material and not is_valid_category(category):
        return None, {"category": [f'"{category}" is not a valid choice.']}
    if not is_material and category:
        return None, {"category": ["A routine filing has no category."]}

    note = data.get("note") or ""
    if not isinstance(note, str):
        return None, {"note": ["note must be text."]}

    return {
        "is_material": is_material,
        "category": category,
        "note": note.strip(),
    }, None


@api_view(["POST"])
@permission_classes([IsAdminUser])
def override_classification(request, classification_id):
    clean, errors = _validate(request.data)
    if errors:
        return Response(errors, status=400)

    try:
        with transaction.atomic():
            classification = (
                FilingClassification.objects
                .select_for_update()
                .filter(pk=classification_id)
                .first()
            )
            if classification is None:
                return Response({"detail": "Not found."}, status=404)

            latest_id = (
                FilingClassification.objects
                .filter(filing_id=classification.filing_id)
                .order_by("-created_at", "-id")
                .values_list("id", flat=True)
                .first()
            )
            if latest_id != classification.id:
                return Response(
                    {"detail": "A newer classification exists for this filing."},
                    status=409,
                )

            existing = (
                ClassificationOverride.objects
                .select_related("reviewer")
                .filter(classification=classification)
                .first()
            )
            if existing is not None:
                return Response(
                    {
                        "detail": (
                            f"Already reviewed by "
                            f"{existing.reviewer.get_username()}."
                        ),
                        "classification": serialize_classification(
                            FilingClassification.objects
                            .select_related("override", "override__reviewer")
                            .get(pk=classification.id)
                        ),
                    },
                    status=409,
                )

            ClassificationOverride.objects.create(
                classification=classification,
                reviewer=request.user,
                taxonomy_version=TAXONOMY_VERSION,
                **clean,
            )
    except IntegrityError:
        return Response(
            {"detail": "Already reviewed by someone else."}, status=409
        )

    return Response(_filing_row(request, classification.filing_id), status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def taxonomy(request):
    return Response(taxonomy_payload())
