# 8-K/A DISABLED: 8-K/A amendment handling was removed, so these tests are
# skipped. The file is kept (not deleted) so it can be re-enabled by
# removing the two lines below.
import unittest
raise unittest.SkipTest("8-K/A DISABLED: amendment handling removed")
from django.contrib.auth import get_user_model
from django.urls import reverse

from rest_framework.test import APIClient

from django.test import TestCase

from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.knowledge_base.models import (
    AutomationRun,
    FailureEvent,
)


class AmendmentReviewApiTests(TestCase):

    def setUp(self):
        self.service = FilingRegistrationService()

        self.original_1 = self.service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000100",
            sequence=1,
            filing_date="2023-06-01",
            primary_document="doc100.htm",
            local_path="/tmp/100",
            source_url="http://test/100",
            report_date="2023-06-01",
        )

        self.original_2 = self.service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000101",
            sequence=1,
            filing_date="2023-06-02",
            primary_document="doc101.htm",
            local_path="/tmp/101",
            source_url="http://test/101",
            report_date="2023-06-01",
        )

        self.amendment = self.service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K/A",
            accession_number="0001-00-000102",
            sequence=1,
            filing_date="2023-06-03",
            primary_document="doc102.htm",
            local_path="/tmp/102",
            source_url="http://test/102",
            report_date="2023-06-01",
        )

        self.amendment.refresh_from_db()

        User = get_user_model()

        self.admin_user = User.objects.create_user(
            username="review_admin",
            password="test-password",
            is_staff=True,
        )

        self.normal_user = User.objects.create_user(
            username="review_user",
            password="test-password",
            is_staff=False,
        )

        self.client = APIClient()

        # W-010: the API now requires a token. Read-only calls run as
        # a normal logged-in user; tests that need admin re-authenticate.
        self.client.force_authenticate(
            user=self.normal_user
        )
    
    def test_ambiguous_amendment_appears_in_review_queue(self):
        response = self.client.get(
            reverse("filings"),
            {
                "status": "review",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        rows = response.json()

        amendment_rows = [
            row
            for row in rows
            if row["id"] == self.amendment.id
        ]

        self.assertEqual(
            len(amendment_rows),
            1,
        )

        row = amendment_rows[0]

        self.assertEqual(
            row["form"],
            "8-K/A",
        )

        self.assertTrue(
            row["flag"]
        )

        self.assertEqual(
            row["flag_reason"],
            "AMBIGUOUS_AMENDMENT_TARGET",
        )

    def test_review_queue_exposes_only_valid_original_candidates(self):
        wrong_report_date = self.service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000103",
            sequence=1,
            filing_date="2023-06-04",
            primary_document="doc103.htm",
            local_path="/tmp/103",
            source_url="http://test/103",
            report_date="2023-06-04",
        )

        wrong_company = self.service.register(
            ticker="OTHER",
            cik="9876543210",
            company_name="Other Co",
            form="8-K",
            accession_number="0002-00-000100",
            sequence=1,
            filing_date="2023-06-01",
            primary_document="other.htm",
            local_path="/tmp/other",
            source_url="http://test/other",
            report_date="2023-06-01",
        )

        response = self.client.get(
            reverse("filings"),
            {
                "status": "review",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        row = next(
            item
            for item in response.json()
            if item["id"] == self.amendment.id
        )

        candidate_ids = {
            candidate["id"]
            for candidate in row["candidate_originals"]
        }

        self.assertEqual(
            candidate_ids,
            {
                self.original_1.id,
                self.original_2.id,
            },
        )

        self.assertNotIn(
            wrong_report_date.id,
            candidate_ids,
        )

        self.assertNotIn(
            wrong_company.id,
            candidate_ids,
        )

    def test_normal_user_cannot_resolve_ambiguous_amendment(self):
        self.client.force_authenticate(
            user=self.normal_user
        )

        response = self.client.post(
            reverse(
                "resolve_amendment",
                args=[self.amendment.id],
            ),
            {
                "original_id": self.original_1.id,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.amendment.refresh_from_db()

        self.assertIsNone(
            self.amendment.amends
        )

        self.assertTrue(
            self.amendment.flag
        )

    def test_admin_can_resolve_valid_candidate(self):
        self.client.force_authenticate(
            user=self.admin_user
        )

        response = self.client.post(
            reverse(
                "resolve_amendment",
                args=[self.amendment.id],
            ),
            {
                "original_id": self.original_1.id,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.amendment.refresh_from_db()

        self.assertEqual(
            self.amendment.amends,
            self.original_1,
        )

        self.assertFalse(
            self.amendment.flag
        )

        self.assertEqual(
            self.amendment.flag_reason,
            "",
        )

        self.assertEqual(
            response.json()["status"],
            "linked",
        )

        self.assertEqual(
            response.json()["original_id"],
            self.original_1.id,
        )

    def test_wrong_company_candidate_is_rejected(self):
        wrong_company = self.service.register(
            ticker="OTHER",
            cik="9876543210",
            company_name="Other Co",
            form="8-K",
            accession_number="0002-00-000101",
            sequence=1,
            filing_date="2023-06-01",
            primary_document="other101.htm",
            local_path="/tmp/other101",
            source_url="http://test/other101",
            report_date="2023-06-01",
        )

        self.client.force_authenticate(
            user=self.admin_user
        )

        response = self.client.post(
            reverse(
                "resolve_amendment",
                args=[self.amendment.id],
            ),
            {
                "original_id": wrong_company.id,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.amendment.refresh_from_db()

        self.assertIsNone(
            self.amendment.amends
        )

    def test_wrong_report_date_candidate_is_rejected(self):
        wrong_report_date = self.service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000104",
            sequence=1,
            filing_date="2023-07-01",
            primary_document="doc104.htm",
            local_path="/tmp/104",
            source_url="http://test/104",
            report_date="2023-07-01",
        )

        self.client.force_authenticate(
            user=self.admin_user
        )

        response = self.client.post(
            reverse(
                "resolve_amendment",
                args=[self.amendment.id],
            ),
            {
                "original_id": wrong_report_date.id,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.amendment.refresh_from_db()

        self.assertIsNone(
            self.amendment.amends
        )

    def test_resolved_amendment_cannot_be_overwritten(self):
        self.client.force_authenticate(
            user=self.admin_user
        )

        first_response = self.client.post(
            reverse(
                "resolve_amendment",
                args=[self.amendment.id],
            ),
            {
                "original_id": self.original_1.id,
            },
            format="json",
        )

        self.assertEqual(
            first_response.status_code,
            200,
        )

        second_response = self.client.post(
            reverse(
                "resolve_amendment",
                args=[self.amendment.id],
            ),
            {
                "original_id": self.original_2.id,
            },
            format="json",
        )

        self.assertEqual(
            second_response.status_code,
            409,
        )

        self.amendment.refresh_from_db()

        self.assertEqual(
            self.amendment.amends,
            self.original_1,
        )

    def test_invalid_original_id_returns_400(self):
        self.client.force_authenticate(
            user=self.admin_user
        )

        response = self.client.post(
            reverse(
                "resolve_amendment",
                args=[self.amendment.id],
            ),
            {
                "original_id": "not-an-id",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_review_queue_still_includes_active_failures(self):
        failed_filing = self.service.register(
            ticker="FAIL",
            cik="1111111111",
            company_name="Failure Co",
            form="8-K",
            accession_number="0003-00-000001",
            sequence=1,
            filing_date="2023-08-01",
            primary_document="failed.htm",
            local_path="/tmp/failed",
            source_url="http://test/failed",
            report_date="2023-08-01",
        )

        FailureEvent.objects.create(
            filing=failed_filing,
            stage=FailureEvent.Stage.SUMMARY,
            code=FailureEvent.Code.SUMMARY_FAILED,
            message="Test summary failure",
        )

        response = self.client.get(
            reverse("filings"),
            {
                "status": "review",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        returned_ids = {
            row["id"]
            for row in response.json()
        }

        self.assertIn(
            failed_filing.id,
            returned_ids,
        )

        self.assertIn(
            self.amendment.id,
            returned_ids,
        )

    def test_automation_run_ambiguous_count_defaults_to_zero(self):
        run = AutomationRun.objects.create()

        self.assertEqual(
            run.ambiguous_amendments_count,
            0,
        )