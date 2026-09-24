from rest_framework import serializers

from watcher.models import (
    AutomationRun,
    Company,
    Filing,
    FilingSummaryCache,
)
from watcher.knowledge_base.ingestion.amendment_linker import (
    AMBIGUOUS_AMENDMENT_TARGET,
    candidate_originals as amendment_candidate_originals,
)


class AutomationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationRun
        fields = "__all__"


class CompanySerializer(serializers.ModelSerializer):

    class Meta:
        model = Company
        fields = [
            "id",
            "ticker",
            "cik",
            "name",
        ]


class FilingSerializer(serializers.ModelSerializer):

    ticker = serializers.CharField(
        source="company.ticker",
        read_only=True,
    )

    company_name = serializers.CharField(
        source="company.name",
        read_only=True,
    )

    amends_accession = serializers.CharField(
        source="amends.accession_number",
        read_only=True,
        allow_null=True,
        default=None,
    )

    amended_by_accession = serializers.SerializerMethodField()

    summary = serializers.SerializerMethodField()

    classification = serializers.SerializerMethodField()

    failure_stage = serializers.SerializerMethodField()
    failure_code = serializers.SerializerMethodField()
    failure_message = serializers.SerializerMethodField()
    failure_created_at = serializers.SerializerMethodField()

    # W-022:
    # Candidate original 8-K filings presented to a reviewer when an
    # amendment cannot be linked automatically without guessing.
    candidate_originals = serializers.SerializerMethodField()

    class Meta:
        model = Filing

        fields = [
            "id",
            "ticker",
            "company_name",
            "form",
            "accession_number",
            "filing_date",
            "report_date",
            "accepted_at",
            "entry_session",
            "entry_rule",
            "parsed_item_codes",
            "primary_document",
            "source_url",
            "ingestion_status",
            "created_at",
            "summary",
            "classification",
            "amends_accession",
            "amended_by_accession",
            "sec_item_codes",
            "item_codes_match",
            "flag",
            "flag_reason",
            "candidate_originals",
            "failure_stage",
            "failure_code",
            "failure_message",
            "failure_created_at",
        ]

    def get_amended_by_accession(self, obj):
        # .all() reuses prefetch_related("amended_by") when the view
        # provides it; otherwise it runs the same query as before.
        return [
            amendment.accession_number
            for amendment in obj.amended_by.all()
        ]

    def get_summary(self, obj):
        try:
            return obj.summary_cache.summary

        except FilingSummaryCache.DoesNotExist:
            return None

    def get_classification(self, obj):
        # Classification remains intentionally non-hardcoded.
        return None

    def _get_active_failure(self, obj):
        if not hasattr(
            obj,
            "_active_failure",
        ):
            # Reuse the batched prefetch from the filings list view.
            prefetched = getattr(
                obj,
                "prefetched_unresolved_failures",
                None,
            )

            if prefetched is not None:
                obj._active_failure = (
                    prefetched[0] if prefetched else None
                )
                return obj._active_failure

            obj._active_failure = (
                obj.failure_events
                .filter(
                    resolved_at__isnull=True
                )
                .order_by(
                    "-created_at"
                )
                .first()
            )

        return obj._active_failure

    def get_failure_stage(self, obj):
        failure = self._get_active_failure(
            obj
        )

        return (
            failure.stage
            if failure
            else None
        )

    def get_failure_code(self, obj):
        failure = self._get_active_failure(
            obj
        )

        return (
            failure.code
            if failure
            else None
        )

    def get_failure_message(self, obj):
        failure = self._get_active_failure(
            obj
        )

        return (
            failure.message
            if failure
            else None
        )

    def get_failure_created_at(self, obj):
        failure = self._get_active_failure(
            obj
        )

        return (
            failure.created_at
            if failure
            else None
        )

    def get_candidate_originals(self, obj):
        """
        W-022:
        Only expose candidate originals when this filing is an unresolved,
        explicitly flagged ambiguous 8-K/A amendment.

        The candidate query is shared with amendment_linker.py so the API
        cannot drift away from the automatic matching policy.
        """

        if (
            obj.form != "8-K/A"
            or obj.amends_id is not None
            or not obj.flag
            or obj.flag_reason
            != AMBIGUOUS_AMENDMENT_TARGET
        ):
            return []

        candidates = (
            amendment_candidate_originals(
                obj
            )
        )

        return [
            {
                "id": candidate.id,
                "accession_number": (
                    candidate.accession_number
                ),
                "filing_date": (
                    candidate.filing_date
                ),
                "report_date": (
                    candidate.report_date
                ),
                "accepted_at": (
                    candidate.accepted_at
                ),
                "source_url": (
                    candidate.source_url
                ),
            }
            for candidate in candidates
        ]