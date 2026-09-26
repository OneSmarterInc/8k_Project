# Append verbatim to the end of watcher/knowledge_base/models.py

class AuditorRun(models.Model):
    """
    One `manage.py audit` run.

    Mirrors InterpreterRun deliberately - same status choices, same
    counter shape - so the stale-run reconciler, the admin and the
    run-history UI all understand it without special cases.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )

    # Provenance. When a number moves, the first question is always
    # "did the system change or did the filings change" - and that is
    # unanswerable without these three.
    taxonomy_version = models.CharField(max_length=20)
    auditor_prompt_version = models.CharField(max_length=20)
    auditor_model_name = models.CharField(max_length=200)

    sampled_count = models.PositiveIntegerField(default=0)
    classification_checked = models.PositiveIntegerField(default=0)
    summary_checked = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"AuditorRun {self.pk} ({self.status})"


class AuditSample(models.Model):
    """
    One audited filing: both checks, because they are drawn from the
    same sample and keeping them together keeps the join simple.

    Foreign-keys FilingClassification and nothing else. The Auditor
    never writes to Filing, FilingClassification or FilingSummaryCache.
    """

    run = models.ForeignKey(
        AuditorRun,
        on_delete=models.CASCADE,
        related_name="samples",
    )
    classification = models.ForeignKey(
        FilingClassification,
        on_delete=models.CASCADE,
        related_name="audit_samples",
    )

    # --- classification check ---
    auditor_category = models.CharField(max_length=40, blank=True)
    auditor_is_material = models.BooleanField(null=True)
    auditor_confidence = models.FloatField(null=True)
    category_agreed = models.BooleanField(null=True)
    material_agreed = models.BooleanField(null=True)
    failure_code = models.CharField(max_length=40, blank=True)

    # --- summary grounding check ---
    summary_claims_total = models.PositiveIntegerField(default=0)
    summary_claims_grounded = models.PositiveIntegerField(default=0)
    ungrounded_claims = models.JSONField(default=list)

    is_sealed = models.BooleanField(default=False)
    input_sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["run", "category_agreed"]),
            models.Index(fields=["classification"]),
            models.Index(fields=["is_sealed"]),
        ]

    @property
    def grounding_rate(self):
        if not self.summary_claims_total:
            return None
        return self.summary_claims_grounded / self.summary_claims_total

    def __str__(self):
        return f"AuditSample {self.pk} (classification {self.classification_id})"


class AuditWindow(models.Model):
    """
    Rolled-up result for one category over one period. This is what the
    alarm reads and what the Accuracy page plots.

    category="" means the overall row for that window.
    """

    run = models.ForeignKey(
        AuditorRun,
        on_delete=models.CASCADE,
        related_name="windows",
    )
    period_start = models.DateField()
    period_end = models.DateField()

    taxonomy_version = models.CharField(max_length=20)
    interpreter_prompt_version = models.CharField(max_length=20, blank=True)
    interpreter_model_name = models.CharField(max_length=200, blank=True)

    category = models.CharField(max_length=40, blank=True)
    sample_size = models.PositiveIntegerField()
    agreement_rate = models.FloatField()
    material_agreement_rate = models.FloatField(null=True)
    grounding_rate = models.FloatField(null=True)
    is_sealed = models.BooleanField(default=False)

    below_bar = models.BooleanField(default=False)
    alarmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-period_end", "category"]
        indexes = [
            models.Index(fields=["category", "-period_end"]),
            models.Index(fields=["below_bar"]),
        ]

    def __str__(self):
        label = self.category or "OVERALL"
        return f"{label} {self.agreement_rate:.2f} (n={self.sample_size})"
