import tempfile
from pathlib import Path

from datetime import date

from django.test import TestCase

from watcher.models import Company, Filing
from watcher.services.ticker_processor import TickerProcessor


class FakeResolver:
    def resolve(self, ticker):
        return {
            "ticker": "AAPL",
            "cik": "320193",
            "name": "Apple Inc.",
        }


class FakeDiscovery:
    """
    W-014: mirrors the real FilingDiscovery contract.

    W-007 changed list_filings to return a shard-aware dict rather than
    a bare list. This double still returned a list, so TickerProcessor's
    `discovery_result.get("filings", [])` raised AttributeError, the
    error was recorded as a discovery failure, and `downloaded` came
    back 0. Production code was correct; the double had drifted.
    """

    def list_filings(self, *, cik, form, start_date=None, end_date=None):
        if form != "8-K":
            return {
                "filings": [],
                "shards_expected": 0,
                "shards_parsed": 0,
                "shard_errors": [],
            }

        return {
            "filings": [
                {
                    "accession_number": "TEST-ACCESSION-001",
                    "primary_document": "test.htm",
                    "filing_date": "2026-09-12",
                }
            ],
            "shards_expected": 0,
            "shards_parsed": 0,
            "shard_errors": [],
        }


class FakeRegistry:
    def __init__(self):
        self.marked = []

    def is_downloaded(
        self,
        cik,
        accession_number,
        sequence,
    ):
        return False

    def mark_downloaded(
        self,
        cik,
        accession_number,
        sequence,
    ):
        self.marked.append(
            (
                cik,
                accession_number,
                sequence,
            )
        )


class FakeDownloader:
    def __init__(self, root):
        self.root = Path(root)

    def get_download_dir(
        self,
        *,
        ticker,
        form,
    ):
        return (
            self.root
            / ticker
            / form
        )

    def resolve_document(
        self,
        *,
        cik,
        accession_number,
        file_type,
        hint_filename,
    ):
        return (
            "https://www.sec.gov/test/",
            {
                "sequence": "1",
                "type": file_type,
                "filename": "test.htm",
            },
        )

    def build_filename(
        self,
        *,
        form,
        file_type,
        filing_date,
        original_filename,
    ):
        return "test.htm"

    def download(
        self,
        **kwargs,
    ):
        path = (
            self.root
            / "AAPL"
            / "8-K"
            / "test.htm"
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            "TEST SEC FILING",
            encoding="utf-8",
        )

        return {
            "path": str(path),
            "url": (
                "https://www.sec.gov/test/"
                "test.htm"
            ),
            "sequence": "1",
        }


class FakeRegistrationService:
    """
    W-014: returns a REAL Filing row.

    With auto_index=True the post-processing stage calls
    registered_filing.save() and hands the row to FailureEvent, so a
    plain object() could never work once W-003/W-036 landed. The double
    now creates the row the real service would have created.
    """

    def __init__(self, company):
        self.calls = 0
        self.company = company
        self.filing = None

    def register(self, **kwargs):
        self.calls += 1

        if self.filing is None:
            self.filing = Filing.objects.create(
                company=self.company,
                accession_number="TEST-ACCESSION-001",
                sequence=1,
                form="8-K",
                filing_date=date(2026, 9, 12),
                primary_document="test.htm",
                source_url="https://www.sec.gov/test/test.htm",
                local_path="/tmp/test.htm",
            )

        return self.filing


class FakeSummaryService:
    """Keeps the LLM summarization stack out of this test (W-011)."""

    def __init__(self):
        self.calls = 0

    def summarize_filing(self, filing_id):
        self.calls += 1
        return {"filing_id": filing_id, "summary": "TEST_SUMMARY"}


class FakeEmailService:
    """Keeps SMTP out of this test."""

    def __init__(self):
        self.calls = 0

    def send(self, **kwargs):
        self.calls += 1
        return False


class FakeIndexingService:
    def __init__(
        self,
        *,
        fail=False,
    ):
        self.calls = 0
        self.received = []
        self.fail = fail

    def index_filing(self, filing):
        self.calls += 1
        self.received.append(filing)

        if self.fail:
            raise RuntimeError(
                "TEST INDEX FAILURE"
            )

        return object()


class TickerProcessorAutoIndexTests(
    TestCase
):
    def setUp(self):
        self.company = Company.objects.create(
            ticker="AAPL",
            cik="320193",
            name="Apple Inc.",
        )

    def _build_processor(
        self,
        *,
        root,
        auto_index,
        indexing_service,
    ):
        registry = FakeRegistry()
        registration = (
            FakeRegistrationService(self.company)
        )

        processor = TickerProcessor(
            resolver=FakeResolver(),
            discovery=FakeDiscovery(),
            downloader=FakeDownloader(root),
            registry=registry,
            registration_service=registration,
            indexing_service=indexing_service,
            summary_service=FakeSummaryService(),
            filing_email_service=FakeEmailService(),
            auto_index=auto_index,
        )

        return (
            processor,
            registry,
            registration,
        )

    def test_auto_index_false_preserves_old_behavior(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            indexing = FakeIndexingService()

            (
                processor,
                registry,
                registration,
            ) = self._build_processor(
                root=root,
                auto_index=False,
                indexing_service=indexing,
            )

            result = processor.process(
                "AAPL"
            )

            self.assertEqual(
                result["downloaded"],
                1,
            )

            self.assertEqual(
                result["failed"],
                0,
            )

            self.assertEqual(
                result["indexed"],
                0,
            )

            self.assertEqual(
                indexing.calls,
                0,
            )

            self.assertEqual(
                registration.calls,
                1,
            )

            self.assertEqual(
                len(registry.marked),
                1,
            )

    def test_auto_index_true_indexes_once(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            indexing = FakeIndexingService()

            (
                processor,
                registry,
                registration,
            ) = self._build_processor(
                root=root,
                auto_index=True,
                indexing_service=indexing,
            )

            result = processor.process(
                "AAPL"
            )

            self.assertEqual(
                result["downloaded"],
                1,
            )

            self.assertEqual(
                result["indexed"],
                1,
            )

            self.assertEqual(
                result["index_failed"],
                0,
            )

            self.assertEqual(
                indexing.calls,
                1,
            )

            self.assertEqual(
                indexing.received[0],
                registration.filing,
            )

            self.assertEqual(
                len(registry.marked),
                1,
            )

    def test_index_failure_does_not_break_download(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            indexing = FakeIndexingService(
                fail=True
            )

            (
                processor,
                registry,
                registration,
            ) = self._build_processor(
                root=root,
                auto_index=True,
                indexing_service=indexing,
            )

            result = processor.process(
                "AAPL"
            )

            self.assertEqual(
                result["downloaded"],
                1,
            )

            self.assertEqual(
                result["failed"],
                0,
            )

            self.assertEqual(
                result["indexed"],
                0,
            )

            self.assertEqual(
                result["index_failed"],
                1,
            )

            self.assertEqual(
                indexing.calls,
                1,
            )

            self.assertEqual(
                registration.calls,
                1,
            )

            self.assertEqual(
                len(registry.marked),
                1,
            )

            errors = (
                result["forms"]["8-K"][
                    "errors"
                ]
            )

            self.assertTrue(
                any(
                    "Knowledge-base indexing failed"
                    in error
                    for error in errors
                )
            )