from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.knowledge_base.models import (
    FailureEvent,
    FilingSummaryCache,
)


class FilingsApiW035Tests(TestCase):
    """W-035: default view shows unsummarized filings; opt-in pagination."""

    def _register(self, n, form="8-K"):
        return self.service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form=form,
            accession_number=f"0009-00-{n:06d}",
            sequence=1,
            filing_date="2026-09-01",
            primary_document=f"doc{n}.htm",
            local_path=f"/tmp/{n}",
            source_url=f"http://test/{n}",
            report_date="2026-09-01",
        )

    def setUp(self):
        self.service = FilingRegistrationService()
        self.client = APIClient()

        self.summarized = self._register(1)
        FilingSummaryCache.objects.create(
            filing=self.summarized,
            content_signature="sig",
            model_name="test",
            prompt_version="v1",
            summary="A summary.",
        )

        self.unsummarized = self._register(2)

        self.failed = self._register(3)
        FailureEvent.objects.create(
            filing=self.failed,
            stage=FailureEvent.Stage.SUMMARY,
            code=FailureEvent.Code.SUMMARY_FAILED,
            message="test failure",
        )

    def _ids(self, params=None):
        response = self.client.get(reverse("filings"), params or {})
        self.assertEqual(response.status_code, 200)
        return {row["id"] for row in response.json()}

    def test_default_includes_unsummarized_and_excludes_failed(self):
        ids = self._ids()
        self.assertIn(self.summarized.id, ids)
        self.assertIn(self.unsummarized.id, ids)
        self.assertNotIn(self.failed.id, ids)

    def test_summarized_keeps_previous_default_behaviour(self):
        self.assertEqual(
            self._ids({"status": "summarized"}),
            {self.summarized.id},
        )

    def test_failed_and_all(self):
        self.assertEqual(self._ids({"status": "failed"}), {self.failed.id})
        self.assertEqual(
            self._ids({"status": "all"}),
            {self.summarized.id, self.unsummarized.id, self.failed.id},
        )

    def test_default_response_is_still_a_plain_list(self):
        response = self.client.get(reverse("filings"))
        self.assertIsInstance(response.json(), list)

    def test_pagination_is_opt_in(self):
        response = self.client.get(
            reverse("filings"), {"status": "all", "page_size": 1}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["results"]), 1)
        self.assertIsNotNone(data["next"])

    def test_sidebar_count_query(self):
        # Sidebar sends status=failed&page_size=1 and reads "count".
        response = self.client.get(
            reverse("filings"), {"status": "failed", "page_size": 1}
        )
        self.assertEqual(response.json()["count"], 1)