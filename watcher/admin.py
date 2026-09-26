"""
Read-only Django admin for the Interpreter and ground-truth tables.

READ-ONLY IS DELIBERATE, NOT DEFENSIVE.

FilingClassification is what the model said. ClassificationOverride is
what a human said instead. GroundTruthLabel is what a labeller said
before seeing any model output. The Auditor measures accuracy by
comparing those three, so a row edited by hand in the admin would
silently corrupt every accuracy number computed from it afterwards,
with no trace of the edit.

Corrections go through the override endpoint, which appends a row and
records who made it. Nothing here may add, change or delete.
"""

from django.contrib import admin

from watcher.models import (
    AuditSample,
    AuditWindow,
    AuditorRun,
    ClassificationOverride,
    FilingClassification,
    GroundTruthLabel,
    GroundTruthSplit,
    InterpreterRun,
    LabellingSample,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """View and filter only. Every write path is closed."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FilingClassification)
class FilingClassificationAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "ticker", "accession", "category", "is_material",
        "confidence", "needs_human_review", "taxonomy_version",
        "prompt_version", "created_at",
    )
    list_filter = (
        "needs_human_review", "category", "is_material",
        "taxonomy_version", "prompt_version", "model_name",
    )
    search_fields = (
        "filing__company__ticker", "filing__accession_number", "category",
    )
    list_select_related = ("filing", "filing__company")
    ordering = ("-created_at",)

    @admin.display(description="Ticker", ordering="filing__company__ticker")
    def ticker(self, obj):
        return obj.filing.company.ticker if obj.filing_id else ""

    @admin.display(description="Accession")
    def accession(self, obj):
        return obj.filing.accession_number if obj.filing_id else ""


@admin.register(ClassificationOverride)
class ClassificationOverrideAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "classification_id", "category", "is_material",
        "reviewer", "taxonomy_version", "created_at",
    )
    list_filter = ("category", "is_material", "taxonomy_version")
    search_fields = ("classification__filing__company__ticker", "note")
    ordering = ("-created_at",)


@admin.register(InterpreterRun)
class InterpreterRunAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "started_at", "completed_at", "status", "filings_selected",
        "classified_count", "review_count", "error_count",
        "taxonomy_version", "prompt_version", "model_name",
    )
    list_filter = ("status", "taxonomy_version", "prompt_version", "model_name")
    ordering = ("-started_at",)


@admin.register(GroundTruthLabel)
class GroundTruthLabelAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "ticker", "labeller", "is_material", "category",
        "confidence", "taxonomy_version", "labelled_at", "duration_seconds",
    )
    list_filter = ("is_material", "category", "taxonomy_version", "labeller")
    search_fields = ("filing__company__ticker", "filing__accession_number", "notes")
    list_select_related = ("filing", "filing__company", "labeller")
    ordering = ("-labelled_at",)

    @admin.display(description="Ticker", ordering="filing__company__ticker")
    def ticker(self, obj):
        return obj.filing.company.ticker if obj.filing_id else ""


@admin.register(LabellingSample)
class LabellingSampleAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "ticker", "position", "stratum", "required_labels",
        "taxonomy_version", "claimed_by", "claimed_at", "created_at",
    )
    list_filter = ("stratum", "required_labels", "taxonomy_version")
    search_fields = ("filing__company__ticker", "filing__accession_number")
    list_select_related = ("filing", "filing__company", "claimed_by")
    ordering = ("position",)

    @admin.display(description="Ticker", ordering="filing__company__ticker")
    def ticker(self, obj):
        return obj.filing.company.ticker if obj.filing_id else ""


@admin.register(GroundTruthSplit)
class GroundTruthSplitAdmin(ReadOnlyAdmin):
    list_display = ("id", "ticker", "split", "assigned_at")
    list_filter = ("split",)
    search_fields = ("filing__company__ticker", "filing__accession_number")
    list_select_related = ("filing", "filing__company")
    ordering = ("-assigned_at",)

    @admin.display(description="Ticker", ordering="filing__company__ticker")
    def ticker(self, obj):
        return obj.filing.company.ticker if obj.filing_id else ""


# ----------------------------------------------------------------------
# Auditor
# ----------------------------------------------------------------------
#
# Read-only for the same reason as everything above: an audit row
# edited by hand corrupts the trend it belongs to, and the trend is
# the only evidence that the Interpreter still works.


@admin.register(AuditorRun)
class AuditorRunAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "started_at", "completed_at", "status", "sampled_count",
        "classification_checked", "summary_checked", "error_count",
        "taxonomy_version", "auditor_prompt_version", "auditor_model_name",
    )
    list_filter = ("status", "taxonomy_version", "auditor_model_name")
    ordering = ("-started_at",)


@admin.register(AuditSample)
class AuditSampleAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "ticker", "interpreter_category", "auditor_category",
        "category_agreed", "material_agreed", "grounding",
        "is_sealed", "failure_code", "created_at",
    )
    list_filter = (
        "category_agreed", "material_agreed", "is_sealed",
        "failure_code", "auditor_category",
    )
    search_fields = (
        "classification__filing__company__ticker",
        "classification__filing__accession_number",
    )
    list_select_related = (
        "classification", "classification__filing",
        "classification__filing__company",
    )
    ordering = ("-created_at",)

    @admin.display(description="Ticker")
    def ticker(self, obj):
        return obj.classification.filing.company.ticker

    @admin.display(description="Interpreter said")
    def interpreter_category(self, obj):
        return obj.classification.category or "ROUTINE"

    @admin.display(description="Grounding")
    def grounding(self, obj):
        rate = obj.grounding_rate
        if rate is None:
            return "-"
        return f"{rate:.0%} ({obj.summary_claims_grounded}/{obj.summary_claims_total})"


@admin.register(AuditWindow)
class AuditWindowAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "period_end", "category", "agreement_rate", "sample_size",
        "grounding_rate", "is_sealed", "below_bar", "alarmed_at",
        "taxonomy_version", "interpreter_prompt_version",
    )
    list_filter = ("below_bar", "is_sealed", "category", "taxonomy_version")
    ordering = ("-period_end", "category")
