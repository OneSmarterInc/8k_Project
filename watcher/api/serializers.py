from rest_framework import serializers

from watcher.models import (
    Company,
    Filing,
    FilingSummaryCache,
    AutomationRun,
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
        read_only=True
    )

    company_name = serializers.CharField(
        source="company.name",
        read_only=True
    )
    
    amends_accession = serializers.CharField(
        source="amends.accession_number",
        read_only=True,
        allow_null=True,
        default=None,
    )

    amended_by_accession = serializers.SerializerMethodField()

    def get_amended_by_accession(self, obj):
        return list(obj.amended_by.values_list("accession_number", flat=True))

    summary = serializers.SerializerMethodField()

    class Meta:

        model = Filing

        fields = [
            "id",
            "ticker",
            "company_name",
            "form",
            "accession_number",
            "filing_date",
            "accepted_at",
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
            "failure_stage",
            "failure_code",
            "failure_message",
            "failure_created_at",
        ]


    def get_summary(self,obj):

        try:
            return obj.summary_cache.summary
        except FilingSummaryCache.DoesNotExist:
            return None

    # classification is handled conditionally depending on backend capability
    classification = serializers.SerializerMethodField()

    def get_classification(self, obj):
        # We don't have a rigid model for classification but we could parse summary or rely on
        # ItemVerificationService. For now, since the user asks not to hardcode it, 
        # return None and frontend will hide it if absent.
        return None

    failure_stage = serializers.SerializerMethodField()
    failure_code = serializers.SerializerMethodField()
    failure_message = serializers.SerializerMethodField()
    failure_created_at = serializers.SerializerMethodField()

    def _get_active_failure(self, obj):
        if not hasattr(obj, "_active_failure"):
            # cache it on the object so we only query once per filing
            obj._active_failure = obj.failure_events.filter(resolved_at__isnull=True).order_by("-created_at").first()
        return obj._active_failure

    def get_failure_stage(self, obj):
        failure = self._get_active_failure(obj)
        return failure.stage if failure else None

    def get_failure_code(self, obj):
        failure = self._get_active_failure(obj)
        return failure.code if failure else None

    def get_failure_message(self, obj):
        failure = self._get_active_failure(obj)
        return failure.message if failure else None

    def get_failure_created_at(self, obj):
        failure = self._get_active_failure(obj)
        return failure.created_at if failure else None