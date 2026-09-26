"""
EX-99 press releases fetched on demand for the Interpreter (exhibits.py).

The download and chunking run through the REAL ExhibitDownloadService and
DocumentIngestionService (never used before this change); only SEC
itself is faked.
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from watcher.interpreter import exhibits
from watcher.interpreter.service import InterpreterService
from watcher.knowledge_base.models import FailureEvent
from watcher.models import FilingChunk, FilingClassification, FilingDocument

from .test_interpreter import FakeGenerator, register

PRESS_RELEASE = (
    "<html><body><p>COSTCO WHOLESALE CORPORATION REPORTS FOURTH QUARTER "
    "AND FISCAL YEAR 2026 OPERATING RESULTS. Net sales for the quarter "
    "increased 8 percent to $84.4 billion.</p></body></html>"
)
CONTRACT = "<html><body><p>CREDIT AGREEMENT dated as of ...</p></body></html>"

SEC_INDEX = [
    {"sequence": "1", "type": "8-K", "filename": "d1.htm", "description": "8-K"},
    {"sequence": "2", "type": "EX-99.1", "filename": "ex991.htm", "description": "Press release"},
    {"sequence": "3", "type": "EX-10.1", "filename": "ex101.htm", "description": "Credit agreement"},
    {"sequence": "4", "type": "GRAPHIC", "filename": "logo.jpg", "description": ""},
]
BODIES = {"ex991.htm": PRESS_RELEASE, "ex101.htm": CONTRACT}


class FakeSecDownloader:
    """Stands in for FilingDownloader: same three methods the service uses."""

    def __init__(self, directory, *, index=SEC_INDEX, fail=False):
        self.directory = Path(directory)
        self.index = index
        self.fail = fail
        self.downloaded = []

    def list_documents(self, cik, accession_number):
        if self.fail:
            raise ConnectionError("SEC unreachable")
        return "https://www.sec.gov/Archives/x/", [dict(d) for d in self.index]

    def build_filename(self, form, document_type, filing_date, document_name):
        return document_name

    def download(self, cik, accession_number, *, local_filename, document, base_url, **_):
        self.downloaded.append(document["type"])
        path = self.directory / local_filename
        path.write_text(BODIES[document["filename"]], encoding="utf-8")
        return {"path": str(path), "url": base_url + document["filename"]}


def answer():
    return json.dumps({
        "is_material": False, "category": None, "confidence": 0.9,
        "body_item_numbers": ["2.02"], "extracted_facts": {},
        "reasoning": "Quarterly results.",
    })


class ExhibitFetchTests(TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.filing = register(1)     # has a PRIMARY chunk, like COST
        self.fake = FakeGenerator(answer())

    def _patch_sec(self, **kwargs):
        downloader = FakeSecDownloader(self.tmp.name, **kwargs)
        patcher = mock.patch(
            "watcher.knowledge_base.ingestion.exhibit_download_service.FilingDownloader",
            return_value=downloader,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return downloader

    def _classify(self):
        return InterpreterService(
            generator=self.fake, threshold=0.7, fetch_exhibits=True
        ).classify(self.filing)

    def test_selector_keeps_only_ex99(self):
        picked = exhibits.PressReleaseSelector().select_exhibits(
            SEC_INDEX, primary_filename="d1.htm"
        )
        self.assertEqual([d["type"] for d in picked], ["EX-99.1"])

    def test_fetches_press_release_only_and_model_reads_it(self):
        sec = self._patch_sec()
        row = self._classify()

        self.assertEqual(sec.downloaded, ["EX-99.1"])            # not EX-10.1
        self.assertTrue(
            FilingDocument.objects.filter(filing=self.filing, document_type="EX-99.1").exists()
        )
        self.assertTrue(exhibits.has_press_release_text(self.filing))

        prompt = self.fake.calls[0][0]
        self.assertIn("Net sales for the quarter", prompt)
        self.assertNotIn("CREDIT AGREEMENT", prompt)
        self.assertEqual(row.model_options["exhibits"], exhibits.FETCHED)
        self.assertEqual(row.model_options["input_documents"], ["PRIMARY DOCUMENT", "EX-99.1"])

    def test_second_run_does_not_download_again(self):
        sec = self._patch_sec()
        self._classify()
        self._classify()
        self.assertEqual(sec.downloaded, ["EX-99.1"])
        self.assertEqual(
            FilingClassification.objects.filter(filing=self.filing)
            .order_by("-id").first().model_options["exhibits"],
            exhibits.PRESENT,
        )

    def test_filing_without_ex99(self):
        sec = self._patch_sec(index=[SEC_INDEX[0], SEC_INDEX[2]])
        row = self._classify()
        self.assertEqual(sec.downloaded, [])
        self.assertEqual(row.model_options["exhibits"], exhibits.NONE_ON_SEC)

    def test_sec_failure_still_classifies_from_primary(self):
        self._patch_sec(fail=True)
        row = self._classify()
        self.assertIsNotNone(row.pk)
        self.assertEqual(row.model_options["exhibits"], exhibits.FAILED)
        self.assertEqual(row.model_options["input_documents"], ["PRIMARY DOCUMENT"])
        self.assertEqual(FailureEvent.objects.count(), 0)

    def test_chunking_failure_is_contained(self):
        self._patch_sec()
        with mock.patch(
            "watcher.knowledge_base.ingestion.document_ingestion_service."
            "DocumentIngestionService.ingest",
            side_effect=RuntimeError("bad html"),
        ):
            row = self._classify()
        self.assertEqual(row.model_options["exhibits"], exhibits.FAILED)

    def test_no_primary_text_means_no_sec_call(self):
        sec = self._patch_sec()
        empty = register(2, with_text=False)
        result = InterpreterService(
            generator=self.fake, threshold=0.7, fetch_exhibits=True
        ).classify(empty)
        self.assertIsNone(result)
        self.assertEqual(sec.downloaded, [])

    @override_settings(INTERPRETER_FETCH_EXHIBITS=False)
    def test_switch_off(self):
        sec = self._patch_sec()
        row = InterpreterService(generator=self.fake, threshold=0.7).classify(self.filing)
        self.assertEqual(sec.downloaded, [])
        self.assertEqual(row.model_options["exhibits"], exhibits.DISABLED)

    def test_switch_default_on(self):
        with override_settings():
            self.assertTrue(exhibits.fetch_enabled())

    def test_primary_chunks_untouched(self):
        before = list(
            FilingChunk.objects.filter(filing=self.filing, document__is_primary=True)
            .values_list("id", "text")
        )
        self._patch_sec()
        self._classify()
        after = list(
            FilingChunk.objects.filter(filing=self.filing, document__is_primary=True)
            .values_list("id", "text")
        )
        self.assertEqual(before, after)
