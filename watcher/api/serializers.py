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