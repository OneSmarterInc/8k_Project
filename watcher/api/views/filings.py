"""Filing list, filtering, pagination and the Review Queue.

Split out of the former single-file watcher/api/views.py. Behaviour is
unchanged.
"""

from django.db import transaction
from django.db.models import OuterRef, Prefetch, Q, Subquery
from django.shortcuts import get_object_or_404

from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from watcher.models import Filing
from watcher.knowledge_base.models import FailureEvent, FilingClassification
# 8-K/A DISABLED: amendment linking removed.
# from watcher.knowledge_base.ingestion.amendment_linker import (
#     AMBIGUOUS_AMENDMENT_TARGET,
#     candidate_originals,
# )

from ..serializers import FilingSerializer


class FilingPagination(PageNumberPagination):
    """
    W-035: opt-in pagination for /api/filings/.

    Used only when the caller sends ?page= or ?page_size=, so every
    existing caller that expects a plain JSON list keeps getting one.
    """

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


@api_view(["GET"])
def filings(request):
    """
    status parameter:
        (none)      - all captured filings, except those with an
                      unresolved failure (those live in the Review Queue).
                      Includes filings that have no summary yet.
        summarized  - previous default: summary exists, no unresolved failure.
        failed      - unresolved failures only.
        review      - failures + ambiguous 8-K/A (Review Queue, W-022).
        classification_review
                    - staff only: filings whose LATEST Interpreter
                      classification needs a human and has no override.
        flagged     - filings the Watcher flagged (flag=True).
        all         - every captured filing.
    """

    queryset = (
        Filing.objects
        # Performance only: load related rows in a few batched queries
        # instead of 3-4 queries per filing. Output is unchanged.
        # 8-K/A DISABLED: "amends" / "amended_by" no longer loaded.
        # .select_related("company", "summary_cache", "amends")
        .select_related("company", "summary_cache")
        .prefetch_related(
            # "amended_by",
            Prefetch(
                "failure_events",
                queryset=(
                    FailureEvent.objects
                    .filter(resolved_at__isnull=True)
                    .order_by("-created_at")
                ),
                to_attr="prefetched_unresolved_failures",
            ),
            # Interpreter: one batched query feeds the `interpretation`
            # field (staff only). Adds no per-row queries.
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
        .order_by("-created_at", "-id")
    )

    unresolved_failure = Q(
        failure_events__isnull=False,
        failure_events__resolved_at__isnull=True,
    )

    status_param = request.GET.get("status")

    if status_param == "failed":
        queryset = queryset.filter(
            unresolved_failure
        ).distinct()

    elif status_param == "review":
        # W-022:
        # Review Queue contains both:
        #
        # 1. filings with unresolved processing failures, and
        # 2. ambiguous 8-K/A filings requiring manual linking.
        # 8-K/A DISABLED: the Review Queue now holds unresolved failures only.
        queryset = queryset.filter(
            unresolved_failure
            # | Q(
            #     form="8-K/A",
            #     amends__isnull=True,
            #     flag=True,
            #     flag_reason=(
            #         AMBIGUOUS_AMENDMENT_TARGET
            #     ),
            # )
        ).distinct()

    elif status_param == "classification_review":
        # Separate from "review" on purpose: "review" already means
        # unresolved failures, and reusing it would mix the two tabs.
        if not request.user.is_staff:
            return Response(
                {"detail": "Classification review is for staff only."},
                status=403,
            )

        latest_id = (
            FilingClassification.objects
            .filter(filing_id=OuterRef("filing_id"))
            .order_by("-created_at", "-id")
            .values("id")[:1]
        )
        needs_review = (
            FilingClassification.objects
            .filter(
                id=Subquery(latest_id),
                needs_human_review=True,
                override__isnull=True,
            )
            .values("filing_id")
        )
        queryset = queryset.filter(id__in=needs_review)

    elif status_param == "summarized":
        queryset = queryset.filter(
            summary_cache__isnull=False
        ).exclude(
            unresolved_failure
        )

    elif status_param == "flagged":
        queryset = queryset.filter(
            flag=True
        )

    elif status_param == "all":
        pass

    else:
        # W-035: default shows every captured filing, summarized or not,
        # except unresolved failures (shown in the Review Queue instead).
        queryset = queryset.exclude(
            unresolved_failure
        )

    ticker = request.GET.get("ticker")
    form = request.GET.get("form")

    if ticker:
        queryset = queryset.filter(
            company__ticker__iexact=ticker
        )

    if form:
        queryset = queryset.filter(
            form=form
        )

    # W-040: pagination is now the DEFAULT, not opt-in.
    #
    # The response was previously unbounded: at 1,500 tickers this
    # endpoint would serialize tens of thousands of rows into a single
    # response. Every response is now capped at page_size rows.
    #
    # Callers are unaffected:
    #   - filingService.normalizeFilingResponse already reads both the
    #     bare-list and the {count,next,previous,results} shapes (FE-003).
    #   - callers that want the whole set (Dashboard, Review Queue) let
    #     getFilings follow "next" for them.
    #   - ?page= / ?page_size= keep working exactly as before.
    paginator = FilingPagination()
    page = paginator.paginate_queryset(queryset, request)

    return paginator.get_paginated_response(
        FilingSerializer(
            page,
            many=True,
            context={"request": request},
        ).data
    )


# 8-K/A DISABLED: manual amendment resolution removed.
# @api_view(["POST"])
# @permission_classes([IsAdminUser])
# 8-K/A DISABLED: manual amendment resolution removed.
# def resolve_amendment(request, filing_id):
#     """
#     W-022:
#     Allow an administrator to manually resolve an ambiguous
#     8-K/A amendment.

#     The selected original filing must satisfy the same candidate
#     matching policy used by amendment_linker.py.
#     """

#     original_id = request.data.get(
#         "original_id"
#     )

#     try:
#         original_id = int(
#             original_id
#         )

#     except (TypeError, ValueError):
#         return Response(
#             {
#                 "status": "error",
#                 "message": (
#                     "original_id must be a valid "
#                     "filing ID."
#                 ),
#             },
#             status=400,
#         )

#     with transaction.atomic():
#         amendment = get_object_or_404(
#             Filing.objects
#             .select_for_update()
#             .select_related("company"),
#             pk=filing_id,
#             form="8-K/A",
#         )

#         # Never allow a second request to overwrite
#         # an already resolved amendment relationship.
#         if amendment.amends_id is not None:
#             return Response(
#                 {
#                     "status": "error",
#                     "message": (
#                         "This amendment is already linked."
#                     ),
#                 },
#                 status=409,
#             )

#         # Manual resolution is only valid for amendments
#         # explicitly marked as ambiguous.
#         if (
#             not amendment.flag
#             or amendment.flag_reason
#             != AMBIGUOUS_AMENDMENT_TARGET
#         ):
#             return Response(
#                 {
#                     "status": "error",
#                     "message": (
#                         "This amendment is not awaiting "
#                         "ambiguous-target review."
#                     ),
#                 },
#                 status=409,
#             )

#         # candidate_originals() enforces:
#         #
#         # - same company
#         # - form == 8-K
#         # - same report_date
#         #
#         # This prevents a reviewer/API caller from linking
#         # an unrelated filing.
#         original = get_object_or_404(
#             candidate_originals(
#                 amendment
#             ),
#             pk=original_id,
#         )

#         amendment.amends = original
#         amendment.flag = False
#         amendment.flag_reason = ""

#         amendment.save(
#             update_fields=[
#                 "amends",
#                 "flag",
#                 "flag_reason",
#                 "updated_at",
#             ]
#         )

#     return Response(
#         {
#             "status": "linked",
#             "filing_id": amendment.id,
#             "original_id": original.id,
#             "amends": (
#                 original.accession_number
#             ),
#         }
#     )