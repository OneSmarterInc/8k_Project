"""
Guide 4.2: labelling API.

GET  /api/labelling/next/    next filing for the caller, claimed for them
POST /api/labelling/labels/  record one label (or a correction)

Blindness is enforced by what these views return: filing identity and
text only. No summary, no flag, no classification, no other label.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from watcher.interpreter.filing_text import build_filing_text
from watcher.interpreter.labelling import (
    LabellingError,
    next_sample_for,
    progress_for,
    record_label,
    user_can_label,
)
from watcher.interpreter.taxonomy import taxonomy_payload


class CanLabel(BasePermission):
    message = "You are not in the labeller group."

    def has_permission(self, request, view):
        return user_can_label(request.user)


def _filing_payload(filing):
    # Deliberately an allow-list. Adding a field here is a decision about
    # what labellers may see; never add summary, flag or classification.
    return {
        "id": filing.id,
        "ticker": filing.company.ticker,
        "company_name": filing.company.name,
        "form": filing.form,
        "accession_number": filing.accession_number,
        "filing_date": filing.filing_date,
        "sec_item_codes": filing.sec_item_codes,
        "source_url": filing.source_url,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated, CanLabel])
def labelling_next(request):
    sample = next_sample_for(request.user)

    body = {
        "taxonomy": taxonomy_payload(),
        "progress": progress_for(request.user),
        "done": sample is None,
        "sample": None,
    }

    if sample is not None:
        text = build_filing_text(sample.filing)
        body["sample"] = {
            "filing": _filing_payload(sample.filing),
            "double_labelled": sample.required_labels > 1,
            "text": text.text,
            "text_truncated": text.truncated,
            "documents": text.documents,
        }

    return Response(body)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanLabel])
def labelling_labels(request):
    try:
        label = record_label(request.user, request.data)
    except LabellingError as exc:
        key = exc.field or "detail"
        return Response({key: [str(exc)]}, status=exc.status)

    return Response(
        {
            "id": label.id,
            "filing_id": label.filing_id,
            "taxonomy_version": label.taxonomy_version,
            "is_material": label.is_material,
            "category": label.category,
            "confidence": label.confidence,
            "supersedes": label.supersedes_id,
            "labelled_at": label.labelled_at,
        },
        status=201,
    )
