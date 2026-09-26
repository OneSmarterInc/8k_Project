"""
Fetch EX-99 exhibits (press releases) for one filing, on demand.

WHY
    The Watcher downloads and chunks only a filing's PRIMARY document.
    For earnings (2.02) and Reg FD (7.01) 8-Ks the primary document is a
    one-paragraph cover note ("a press release is attached as Exhibit
    99.1"); the substance lives in EX-99.1. Found on COST 0000909832-26-
    000084, where the Interpreter received 1,048 characters.

HOW (reuse only)
    The repo already had both halves, never wired in:
        ExhibitDownloadService   lists the filing on SEC, downloads, and
                                 registers FilingDocument rows
        DocumentIngestionService chunks one FilingDocument (safe re-run)
    This module calls them for EX-99 only. EX-10 contracts, EX-4
    instruments, legal opinions etc. are not fetched: long, and they say
    what the paper is, not what the company did.

SCOPE
    Interpreter only. The Watcher, its ingestion and the summary are not
    changed. Downloads go through the existing rate-limited SEC client.

Never raises: a failure returns a status and the Interpreter classifies
from the primary document, exactly as before.
"""

import logging
import os

from django.conf import settings

from watcher.knowledge_base.ingestion.document_selector import (
    FilingDocumentSelector,
)
from watcher.models import FilingChunk

logger = logging.getLogger(__name__)

EXHIBIT_PREFIX = "EX-99"

# Statuses recorded on the classification row (model_options["exhibits"]).
PRESENT = "present"          # EX-99 text was already chunked
FETCHED = "fetched"          # downloaded + chunked now
NONE_ON_SEC = "none_on_sec"  # the filing has no EX-99 exhibit
FAILED = "failed"            # SEC / download / chunking error
DISABLED = "disabled"        # INTERPRETER_FETCH_EXHIBITS is off

_TRUE = {"1", "true", "yes", "on"}


def fetch_enabled():
    """INTERPRETER_FETCH_EXHIBITS, default ON."""
    value = getattr(settings, "INTERPRETER_FETCH_EXHIBITS", None)
    if value is None:
        value = os.getenv("INTERPRETER_FETCH_EXHIBITS")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


class PressReleaseSelector(FilingDocumentSelector):
    """The existing selector, narrowed to EX-99.x."""

    def select_exhibits(self, documents, *, primary_filename=None):
        return [
            document
            for document in super().select_exhibits(
                documents, primary_filename=primary_filename,
            )
            if str(document.get("type") or "").strip().upper().startswith(
                EXHIBIT_PREFIX
            )
        ]


def has_press_release_text(filing):
    return FilingChunk.objects.filter(
        filing=filing,
        document__document_type__istartswith=EXHIBIT_PREFIX,
    ).exists()


def ensure_press_release_exhibits(
    filing,
    *,
    download_service=None,
    ingestion_service=None,
):
    """Returns one of the status constants above. Never raises."""
    if has_press_release_text(filing):
        return PRESENT

    try:
        if download_service is None:
            from watcher.knowledge_base.ingestion.exhibit_download_service import (
                ExhibitDownloadService,
            )
            download_service = ExhibitDownloadService(
                selector=PressReleaseSelector()
            )

        if ingestion_service is None:
            from watcher.knowledge_base.ingestion.document_ingestion_service import (
                DocumentIngestionService,
            )
            ingestion_service = DocumentIngestionService()

        results = download_service.download_for_filing(filing)

    except Exception:
        logger.exception(
            "EX-99 download failed for filing %s", filing.pk
        )
        return FAILED

    documents = [
        result["document"]
        for result in results or []
        if result.get("document") is not None
    ]

    if not documents:
        return NONE_ON_SEC

    ingested = 0
    for document in documents:
        try:
            ingestion_service.ingest(document)
            ingested += 1
        except Exception:
            logger.exception(
                "EX-99 chunking failed for document %s (filing %s)",
                document.pk,
                filing.pk,
            )

    return FETCHED if ingested else FAILED
