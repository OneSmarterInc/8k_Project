import shutil
import tempfile

from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from watcher.knowledge_base.models import (
    Company,
    Filing,
    FilingChunk,
    FilingDocument,
)
from watcher.knowledge_base.summarization.company_summary_service import (
    CompanySummaryService,
)
from watcher.knowledge_base.summarization.document_summary_service import (
    DocumentSummaryService,
)
from watcher.knowledge_base.summarization.filing_summary_service import (
    FilingSummaryService,
)


class FakeGenerationService:
    """
    W-014: mirrors OllamaGenerationService.generate.

    The real signature grew a keyword-only `max_tokens`, and several
    summarization services pass it. This double had not kept up, so
    every call raised TypeError. Production code was correct.
    """

    def generate(self, prompt, *, temperature=0.0, max_tokens=512):
        return "TEST_SUMMARY"


GENERATION_MODULES = (
    "watcher.knowledge_base.summarization.company_summary_service",
    "watcher.knowledge_base.summarization.document_fallback_summary_service",
    "watcher.knowledge_base.summarization.document_summary_service",
    "watcher.knowledge_base.summarization.filing_summary_service",
    "watcher.knowledge_base.summarization.section_summary_service",
    "watcher.knowledge_base.summarization.summary_validator",
)


class SummarizationServiceTests(TestCase):

    def _doc_file(self, name):
        path = self.doc_root / name
        if not path.exists():
            path.write_text(
                "<html><body>Test SEC document body.</body></html>",
                encoding="utf-8",
            )
        return path

    def setUp(self):
        self.generator = FakeGenerationService()

        # W-014: the local_path values were hardcoded Windows paths
        # ("C:/test/..."). DocumentSummaryService now reads the file off
        # disk, so those tests only passed on a machine that happened to
        # have C:\test. Real temp files make the test portable.
        self.doc_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.doc_root, True)

        # W-014: injecting generation_service into the top-level service
        # does not reach SectionSummaryService or SummaryValidator, which
        # each construct their own OllamaGenerationService. Without this
        # the test opens a real HTTP connection to 127.0.0.1:11434 and
        # its result depends on whether Ollama happens to be running.
        # Patching the symbol in each module keeps the test hermetic
        # without touching production wiring (W-011 owns that).
        for module_path in GENERATION_MODULES:
            patcher = patch(
                f"{module_path}.OllamaGenerationService",
                return_value=self.generator,
            )
            patcher.start()
            self.addCleanup(patcher.stop)

        self.company = Company.objects.create(
            ticker="TEST",
            cik="0000000001",
            name="Test Company",
        )

        self.filing_8k = Filing.objects.create(
            company=self.company,
            accession_number="0000000001-26-000001",
            sequence=1,
            form="8-K",
            filing_date=date(2026, 1, 10),
            primary_document="test-8k.htm",
            source_url=(
                "https://www.sec.gov/test/test-8k.htm"
            ),
            local_path=str(self._doc_file("test-8k.htm")),
        )

        self.primary_document = FilingDocument.objects.create(
            filing=self.filing_8k,
            sequence="1",
            document_type="PRIMARY",
            document_name="test-8k.htm",
            source_url=(
                "https://www.sec.gov/test/test-8k.htm"
            ),
            local_path=str(self._doc_file("test-8k.htm")),
            content_sha256="a" * 64,
            is_primary=True,
        )

        self.exhibit_document = FilingDocument.objects.create(
            filing=self.filing_8k,
            sequence="2",
            document_type="EX-99.1",
            document_name="test-ex991.htm",
            source_url=(
                "https://www.sec.gov/test/test-ex991.htm"
            ),
            local_path=str(self._doc_file("test-ex991.htm")),
            content_sha256="b" * 64,
            is_primary=False,
        )

        FilingChunk.objects.create(
            filing=self.filing_8k,
            document=self.primary_document,
            chunk_index=0,
            item_number="Item 2.02",
            section_title=(
                "Results of Operations and Financial Condition"
            ),
            text="Primary filing financial results.",
            content_sha256="c" * 64,
        )

        FilingChunk.objects.create(
            filing=self.filing_8k,
            document=self.exhibit_document,
            chunk_index=1,
            item_number="",
            section_title="Press Release",
            text="Exhibit financial results.",
            content_sha256="d" * 64,
        )

        self.filing_10q = Filing.objects.create(
            company=self.company,
            accession_number="0000000001-26-000002",
            sequence=1,
            form="10-Q",
            filing_date=date(2026, 4, 10),
            primary_document="test-10q.htm",
            source_url=(
                "https://www.sec.gov/test/test-10q.htm"
            ),
            local_path=str(self._doc_file("test-10q.htm")),
        )

        self.document_10q = FilingDocument.objects.create(
            filing=self.filing_10q,
            sequence="1",
            document_type="PRIMARY",
            document_name="test-10q.htm",
            source_url=(
                "https://www.sec.gov/test/test-10q.htm"
            ),
            local_path=str(self._doc_file("test-10q.htm")),
            content_sha256="e" * 64,
            is_primary=True,
        )

        FilingChunk.objects.create(
            filing=self.filing_10q,
            document=self.document_10q,
            chunk_index=0,
            item_number="Item 2",
            section_title="Management Discussion",
            text="Quarterly operating results.",
            content_sha256="f" * 64,
        )

    def test_document_summary_uses_all_document_chunks(self):
        service = DocumentSummaryService(
            generation_service=self.generator
        )

        result = service.summarize_document(
            self.primary_document.id
        )

        self.assertEqual(result.ticker, "TEST")
        self.assertEqual(result.form, "8-K")
        self.assertEqual(result.chunk_count, 1)

        # W-014: this line used to assert the summary was exactly the
        # generator's raw output. SummaryComposer now builds a
        # structured, validated summary with a header, so raw
        # pass-through is no longer the contract. The assertion is
        # relaxed to what is still true rather than deleted, and the
        # three assertions above remain the meaningful coverage.
        # Revisit when W-011 settles the summarization boundary.
        self.assertTrue(result.summary)
        self.assertIn("TEST", result.summary)
        self.assertEqual(
            result.source_url,
            "https://www.sec.gov/test/test-8k.htm",
        )

    def test_filing_summary_includes_primary_and_exhibit(self):
        service = FilingSummaryService(
            generation_service=self.generator
        )

        result = service.summarize_filing(
            self.filing_8k.id
        )

        self.assertEqual(result.ticker, "TEST")
        self.assertEqual(result.document_count, 2)
        self.assertEqual(result.chunk_count, 2)
        self.assertEqual(len(result.source_urls), 2)
        self.assertEqual(result.summary, "TEST_SUMMARY")

    def test_company_summary_uses_all_filings_and_filters(self):
        service = CompanySummaryService(
            generation_service=self.generator
        )

        result = service.summarize_company("TEST")

        self.assertEqual(result.filing_count, 2)
        self.assertEqual(result.document_count, 3)
        self.assertEqual(result.chunk_count, 3)
        self.assertEqual(result.summary, "TEST_SUMMARY")

        filtered = service.summarize_company(
            "TEST",
            form="10-Q",
        )

        self.assertEqual(filtered.filing_count, 1)
        self.assertEqual(filtered.form_filter, "10-Q")
        self.assertEqual(
            filtered.filings[0].form,
            "10-Q",
        )