from django.db import models
# from pgvector.django import VectorField


class Company(models.Model):
    ticker = models.CharField(
        max_length=16,
        unique=True,
    )

    cik = models.CharField(
        max_length=10,
        unique=True,
    )

    name = models.CharField(
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["ticker"]

    def __str__(self):
        return f"{self.ticker} ({self.cik})"


class Filing(models.Model):
    class IngestionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        CHUNKED = "chunked", "Chunked"
        INDEXED = "indexed", "Indexed"
        FAILED = "failed", "Failed"

    automation_run = models.ForeignKey(
        "AutomationRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="filings",
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="filings",
    )

    accession_number = models.CharField(
        max_length=32,
    )

    amends = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="amended_by",
        help_text="The original filing this amendment supersedes",
    )

    sequence = models.PositiveIntegerField(
        default=1,
    )

    form = models.CharField(
        max_length=10,
    )

    filing_date = models.DateField(
        null=True,
        blank=True,
    )

    report_date = models.DateField(
        null=True,
        blank=True,
    )

    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    entry_session = models.DateField(
        null=True,
        blank=True,
        db_index=True,
    )
    entry_rule = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        db_index=True,
    )
    sec_item_codes = models.TextField(
        blank=True,
        default="",
        help_text=(
            "SEC reported 8-K item codes. "
            "Example: 1.01;9.01"
        ),
    )

    parsed_item_codes = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Item codes extracted by internal parser. "
            "Example: 1.01;9.01"
        ),
    )

    item_codes_match = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "Whether SEC reported items match "
            "parsed item codes."
        ),
    )
    primary_document = models.CharField(
        max_length=255,
        blank=True,
    )

    source_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    local_path = models.TextField()

    content_sha256 = models.CharField(
        max_length=64,
        blank=True,
    )

    file_size = models.BigIntegerField(
        null=True,
        blank=True,
    )

    ingestion_status = models.CharField(
        max_length=20,
        choices=IngestionStatus.choices,
        default=IngestionStatus.PENDING,
        db_index=True,
    )
    flag = models.BooleanField(
        default=False,
        db_index=True,
    )

    flag_reason = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )
    downloaded_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    email_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "accession_number",
                    "sequence",
                ],
                name="uq_filing_identity",
            )
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "form",
                    "filing_date",
                ],
                name="idx_filing_lookup",
            ),
        ]

    def __str__(self):
        return (
            f"{self.company.ticker} "
            f"{self.form} "
            f"{self.accession_number}"
        )


class EmbeddingVersion(models.Model):
    provider = models.CharField(
        max_length=50,
        default="ollama",
    )

    model_name = models.CharField(
        max_length=255,
    )

    dimensions = models.PositiveIntegerField()

    chunker_version = models.CharField(
        max_length=50,
    )

    is_active = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "provider",
                    "model_name",
                    "dimensions",
                    "chunker_version",
                ],
                name="uq_embedding_version",
            )
        ]

    def __str__(self):
        return (
            f"{self.provider}:"
            f"{self.model_name} "
            f"({self.dimensions})"
        )


class FilingChunk(models.Model):
    filing = models.ForeignKey(
        Filing,
        on_delete=models.CASCADE,
        related_name="chunks",
    )

    chunk_index = models.PositiveIntegerField()

    item_number = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
    )

    section_title = models.CharField(
        max_length=500,
        blank=True,
    )

    text = models.TextField()

    content_sha256 = models.CharField(
        max_length=64,
        db_index=True,
    )

    char_start = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    char_end = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    document = models.ForeignKey(
        "FilingDocument",
        on_delete=models.CASCADE,
        related_name="chunks",
        null=True,
        blank=True,
    )
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "document",
                    "chunk_index",
                ],
                name="uq_document_chunk",
            )
        ]

        ordering = [
            "filing_id",
            "chunk_index",
        ]

    def __str__(self):
        return (
            f"{self.filing_id}:"
            f"{self.chunk_index}"
        )

class FilingDocument(models.Model):
    filing = models.ForeignKey(
        Filing,
        on_delete=models.CASCADE,
        related_name="documents",
    )

    sequence = models.CharField(
        max_length=32,
        blank=True,
    )

    document_type = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
    )

    document_name = models.CharField(
        max_length=255,
    )

    description = models.CharField(
        max_length=500,
        blank=True,
    )

    source_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    local_path = models.TextField(
        blank=True,
    )

    content_sha256 = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )

    file_size = models.BigIntegerField(
        null=True,
        blank=True,
    )

    is_primary = models.BooleanField(
        default=False,
        db_index=True,
    )

    downloaded_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "filing",
                    "sequence",
                    "document_name",
                ],
                name="uq_filing_document_identity",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "filing",
                    "document_type",
                ],
                name="idx_filing_document_type",
            ),
        ]

        ordering = [
            "filing_id",
            "sequence",
            "id",
        ]

    def __str__(self):
        return (
            f"{self.filing} "
            f"{self.document_type} "
            f"{self.document_name}"
        )
    
class ChunkEmbedding(models.Model):
    chunk = models.ForeignKey(
        FilingChunk,
        on_delete=models.CASCADE,
        related_name="embeddings",
    )

    embedding_version = models.ForeignKey(
        EmbeddingVersion,
        on_delete=models.PROTECT,
        related_name="chunk_embeddings",
    )

    # vector = VectorField(
    #     dimensions=768,
    # )
    vector = models.JSONField(
        default=list,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "chunk",
                    "embedding_version",
                ],
                name="uq_chunk_embedding_version",
            )
        ]

    def __str__(self):
        return (
            f"Chunk {self.chunk_id} - "
            f"{self.embedding_version.model_name}"
        )


class IngestionJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    filing = models.OneToOneField(
        Filing,
        on_delete=models.CASCADE,
        related_name="ingestion_job",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    attempt_count = models.PositiveIntegerField(
        default=0,
    )

    last_error = models.TextField(
        blank=True,
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return (
            f"{self.filing_id}:"
            f"{self.status}"
        )

class CompanyAlias(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="aliases",
    )

    alias = models.CharField(
        max_length=255,
    )

    normalized_alias = models.CharField(
        max_length=255,
        db_index=True,
    )

    source = models.CharField(
        max_length=50,
        default="system",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "normalized_alias",
                ],
                name="uq_company_normalized_alias",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "normalized_alias",
                    "is_active",
                ],
                name="idx_company_alias_lookup",
            ),
        ]

        ordering = [
            "company_id",
            "normalized_alias",
        ]

    def __str__(self):
        return (
            f"{self.company.ticker}: "
            f"{self.alias}"
        )

class FilingSummaryCache(models.Model):
    filing = models.OneToOneField(
        Filing,
        on_delete=models.CASCADE,
        related_name="summary_cache",
    )

    content_signature = models.CharField(
        max_length=64,
        db_index=True,
    )

    model_name = models.CharField(
        max_length=255,
    )

    prompt_version = models.CharField(
        max_length=50,
    )

    summary = models.TextField()

    document_count = models.PositiveIntegerField(
        default=0,
    )

    chunk_count = models.PositiveIntegerField(
        default=0,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "model_name",
                    "prompt_version",
                ],
                name="idx_filing_summary_version",
            ),
        ]

    def __str__(self):
        return (
            f"{self.filing} "
            f"[{self.model_name} / "
            f"{self.prompt_version}]"
        )
class DocumentSummaryCache(models.Model):
    """
    Stores one concise generated summary for one SEC filing document.

    The original document text remains in FilingDocument/FilingChunk.
    This table stores only the derived summary and cache metadata.
    """

    document = models.OneToOneField(
        FilingDocument,
        on_delete=models.CASCADE,
        related_name="summary_cache",
    )

    content_signature = models.CharField(
        max_length=64,
        db_index=True,
    )

    model_name = models.CharField(
        max_length=255,
    )

    prompt_version = models.CharField(
        max_length=50,
    )

    summary = models.TextField()

    chunk_count = models.PositiveIntegerField(
        default=0,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "model_name",
                    "prompt_version",
                ],
                name="idx_document_summary_version",
            ),
        ]

    def __str__(self):
        return (
            f"{self.document} "
            f"[{self.model_name} / "
            f"{self.prompt_version}]"
        )


class AutomationRun(models.Model):
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
    files_detected = models.PositiveIntegerField(default=0)
    files_processed = models.PositiveIntegerField(default=0)
    shards_expected = models.PositiveIntegerField(default=0)
    shards_parsed = models.PositiveIntegerField(default=0)
    summary_generated_count = models.PositiveIntegerField(default=0)
    email_sent_count = models.PositiveIntegerField(default=0)
    unlinked_amendments_count = models.PositiveIntegerField(default=0)

    # W-022:
    # Number of unresolved 8-K/A amendments where multiple
    # possible original 8-K filings exist.
    ambiguous_amendments_count = models.PositiveIntegerField(
        default=0
    )

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Run {self.id} - {self.status}"

class ScheduleConfig(models.Model):
    frequency = models.CharField(max_length=20, default='daily')
    start_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    sync_zone = models.BooleanField(default=False)
    
    daily_recur = models.IntegerField(default=1)
    
    weekly_recur = models.IntegerField(default=1)
    weekly_days = models.JSONField(default=dict)
    
    monthly_type = models.CharField(max_length=20, default='days')
    monthly_months = models.CharField(max_length=50, default='All months')
    monthly_days = models.CharField(max_length=100, default='1')
    monthly_on_week = models.CharField(max_length=20, default='First')
    monthly_on_day = models.CharField(max_length=20, default='Sunday')
    
    run_count = models.IntegerField(default=1)
    run_times = models.JSONField(default=list)
    
    is_active = models.BooleanField(default=False)

    # ------------------------------------------------------------------
    # W-037 (R-08 / R-09): intraday interval polling + nightly sweep.
    #
    # These fields are only read when frequency == "interval".
    # Every pre-existing frequency (onetime / daily / weekly / monthly)
    # ignores them completely, so adding them cannot change the behaviour
    # of an existing ScheduleConfig row.
    # ------------------------------------------------------------------
    interval_minutes = models.PositiveIntegerField(default=20)
    active_window_start = models.TimeField(null=True, blank=True)
    active_window_end = models.TimeField(null=True, blank=True)
    nightly_sweep = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'Schedule Config'
class FailureEvent(models.Model):

    class Stage(models.TextChoices):
        DISCOVERY = "discovery", "Discovery"
        DOWNLOAD = "download", "Download"
        METADATA = "metadata", "Metadata"
        REGISTRATION = "registration", "Registration"
        INDEXING = "indexing", "Indexing"
        SUMMARY = "summary", "Summary"
        EMAIL = "email", "Email"
        INGESTION = "ingestion", "Ingestion"


    class Code(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        SHARD_FETCH_FAILED = (
            "shard_fetch_failed",
            "Shard Fetch Failed",
        )
        DOWNLOAD_FAILED = (
            "download_failed",
            "Download Failed",
        )
        METADATA_FAILED = (
            "metadata_failed",
            "Metadata Failed",
        )
        REGISTRATION_FAILED = (
            "registration_failed",
            "Registration Failed",
        )
        INDEX_FAILED = (
            "index_failed",
            "Index Failed",
        )
        SUMMARY_FAILED = (
            "summary_failed",
            "Summary Failed",
        )
        EMAIL_FAILED = (
            "email_failed",
            "Email Failed",
        )
        INGESTION_FAILED = (
            "ingestion_failed",
            "Ingestion Failed",
        )


    filing = models.ForeignKey(
        Filing,
        on_delete=models.CASCADE,
        related_name="failure_events",
        null=True,
        blank=True,
    )


    stage = models.CharField(
        max_length=30,
        choices=Stage.choices,
    )


    code = models.CharField(
        max_length=50,
        choices=Code.choices,
        default=Code.UNKNOWN,
    )


    message = models.TextField()


    created_at = models.DateTimeField(
        auto_now_add=True,
    )


    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    def __str__(self):
        return (
            f"{self.stage}: {self.code}"
        )

class SMTPConfig(models.Model):
    reply_to_email = models.EmailField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Security(models.TextChoices):
        TLS = "TLS", "TLS"
        STARTTLS = "STARTTLS", "STARTTLS"
        SSL = "SSL", "SSL"
        NONE = "NONE", "None"

    sender_name = models.CharField(max_length=200, blank=True)
    host = models.CharField(max_length=253)
    port = models.PositiveIntegerField(default=587)
    security = models.CharField(
        max_length=10,
        choices=Security.choices,
        default=Security.TLS,
    )
    username = models.CharField(max_length=254, blank=True)
    sender_email = models.EmailField()
    reply_to_email = models.EmailField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SMTP configuration"
        verbose_name_plural = "SMTP configuration"

    def __str__(self):
        return f"{self.host}:{self.port} ({self.security})"


class TOTPDevice(models.Model):
    """
    MFA-01: a TOTP authenticator binding for one user.

    Opt-in. A user with no CONFIRMED device logs in exactly as before,
    which is what makes this safe to deploy without a migration window.

    `confirmed` is the gate. The secret is generated at setup but the
    device does nothing until the user proves they scanned it by
    submitting a valid code. Without that, a failed enrolment would
    lock the account out.

    `last_used_step` prevents replay. A TOTP code stays valid for its
    whole 30-second step, so a code observed in transit could be
    re-sent. Recording the step it was used at and refusing anything
    at or below it closes that.
    """

    user = models.OneToOneField(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="totp_device",
    )

    secret = models.CharField(max_length=64)

    confirmed = models.BooleanField(default=False)

    last_used_step = models.BigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "TOTP device"

    def __str__(self):
        state = "confirmed" if self.confirmed else "pending"
        return f"TOTP device for {self.user} ({state})"


class BackupCode(models.Model):
    """
    MFA-01: one single-use recovery code.

    Without these a lost phone is a permanent lockout with no recovery
    path. Stored HASHED with Django's password hasher, shown to the
    user exactly once at enrolment.
    """

    user = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="backup_codes",
    )

    code_hash = models.CharField(max_length=128)

    used_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "used_at"]),
        ]

    def __str__(self):
        return f"Backup code for {self.user}"