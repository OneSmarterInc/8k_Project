from django.test import TestCase

from watcher.knowledge_base.ingestion.amendment_linker import (
    link_amendment,
)
from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.knowledge_base.models import Company


class FilingAmendsTests(TestCase):

    def test_multiple_amendments_to_same_original(self):
        """
        Existing W-019/W-027 regression coverage.

        Multiple amendments may point to the same original 8-K.
        """

        service = FilingRegistrationService()

        Company.objects.create(
            ticker="TEST",
            cik="1234567890",
            name="Test Co",
        )

        original = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000001",
            sequence=1,
            filing_date="2023-01-01",
            primary_document="doc1.htm",
            local_path="/tmp/1",
            source_url="http://test/1",
            report_date="2023-01-01",
        )

        amendment1 = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K/A",
            accession_number="0001-00-000002",
            sequence=1,
            filing_date="2023-01-02",
            primary_document="doc2.htm",
            local_path="/tmp/2",
            source_url="http://test/2",
            report_date="2023-01-01",
        )

        amendment2 = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K/A",
            accession_number="0001-00-000003",
            sequence=1,
            filing_date="2023-01-03",
            primary_document="doc3.htm",
            local_path="/tmp/3",
            source_url="http://test/3",
            report_date="2023-01-01",
        )

        self.assertEqual(
            amendment1.amends,
            original,
        )

        self.assertEqual(
            amendment2.amends,
            original,
        )

        amended_by = list(
            original.amended_by.all()
        )

        self.assertEqual(
            len(amended_by),
            2,
        )

        self.assertIn(
            amendment1,
            amended_by,
        )

        self.assertIn(
            amendment2,
            amended_by,
        )

    def test_two_originals_same_report_date_is_flagged_not_guessed(self):
        """
        W-022 regression coverage.

        When two possible originals exist, the linker must never
        guess which filing the amendment belongs to.
        """

        service = FilingRegistrationService()

        service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000010",
            sequence=1,
            filing_date="2023-02-01",
            primary_document="doc10.htm",
            local_path="/tmp/10",
            source_url="http://test/10",
            report_date="2023-02-01",
        )

        service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000011",
            sequence=1,
            filing_date="2023-02-02",
            primary_document="doc11.htm",
            local_path="/tmp/11",
            source_url="http://test/11",
            report_date="2023-02-01",
        )

        amendment = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K/A",
            accession_number="0001-00-000012",
            sequence=1,
            filing_date="2023-02-03",
            primary_document="doc12.htm",
            local_path="/tmp/12",
            source_url="http://test/12",
            report_date="2023-02-01",
        )

        amendment.refresh_from_db()

        self.assertIsNone(
            amendment.amends
        )

        self.assertTrue(
            amendment.flag
        )

        self.assertEqual(
            amendment.flag_reason,
            "AMBIGUOUS_AMENDMENT_TARGET",
        )

    def test_amendment_before_original_is_linked_later(self):
        """
        W-026 / W-020 regression coverage.

        Discovery can encounter an 8-K/A before its original 8-K.
        The amendment must initially remain unlinked and then link
        successfully when the reconciliation sweep retries it after
        the original exists.
        """

        service = FilingRegistrationService()

        # Amendment arrives first.
        amendment = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K/A",
            accession_number="0001-00-000020",
            sequence=1,
            filing_date="2023-03-05",
            primary_document="doc20.htm",
            local_path="/tmp/20",
            source_url="http://test/20",
            report_date="2023-03-01",
        )

        amendment.refresh_from_db()

        # There is no original yet, so registration must not invent one.
        self.assertIsNone(
            amendment.amends
        )

        before_original = link_amendment(
            amendment
        )

        self.assertFalse(
            before_original.linked
        )

        self.assertEqual(
            before_original.reason,
            "NO_ORIGINAL",
        )

        # Original arrives later.
        original = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000021",
            sequence=1,
            filing_date="2023-03-02",
            primary_document="doc21.htm",
            local_path="/tmp/21",
            source_url="http://test/21",
            report_date="2023-03-01",
        )

        # This is the same operation used by the watcher
        # reconciliation sweep.
        result = link_amendment(
            amendment
        )

        amendment.refresh_from_db()

        self.assertTrue(
            result.linked
        )

        self.assertEqual(
            result.reason,
            "",
        )

        self.assertEqual(
            amendment.amends,
            original,
        )

    def test_amendment_without_report_date_is_left_unlinked(self):
        """
        W-026 regression coverage.

        Matching requires report_date. An amendment without it must
        remain unlinked rather than being guessed from other fields.
        """

        service = FilingRegistrationService()

        # An original exists, but the amendment lacks the matching key.
        service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000030",
            sequence=1,
            filing_date="2023-04-02",
            primary_document="doc30.htm",
            local_path="/tmp/30",
            source_url="http://test/30",
            report_date="2023-04-01",
        )

        amendment = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K/A",
            accession_number="0001-00-000031",
            sequence=1,
            filing_date="2023-04-05",
            primary_document="doc31.htm",
            local_path="/tmp/31",
            source_url="http://test/31",
            report_date=None,
        )

        result = link_amendment(
            amendment
        )

        amendment.refresh_from_db()

        self.assertFalse(
            result.linked
        )

        self.assertEqual(
            result.reason,
            "NO_REPORT_DATE",
        )

        self.assertIsNone(
            amendment.amends
        )

    def test_link_amendment_is_idempotent(self):
        """
        W-026 regression coverage.

        Calling the linker repeatedly must not change an already-correct
        relationship or create additional side effects.
        """

        service = FilingRegistrationService()

        original = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K",
            accession_number="0001-00-000040",
            sequence=1,
            filing_date="2023-05-02",
            primary_document="doc40.htm",
            local_path="/tmp/40",
            source_url="http://test/40",
            report_date="2023-05-01",
        )

        amendment = service.register(
            ticker="TEST",
            cik="1234567890",
            company_name="Test Co",
            form="8-K/A",
            accession_number="0001-00-000041",
            sequence=1,
            filing_date="2023-05-05",
            primary_document="doc41.htm",
            local_path="/tmp/41",
            source_url="http://test/41",
            report_date="2023-05-01",
        )

        amendment.refresh_from_db()

        first_result = link_amendment(
            amendment
        )

        second_result = link_amendment(
            amendment
        )

        amendment.refresh_from_db()

        self.assertTrue(
            first_result.linked
        )

        self.assertTrue(
            second_result.linked
        )

        self.assertEqual(
            amendment.amends,
            original,
        )

        self.assertEqual(
            original.amended_by.filter(
                pk=amendment.pk
            ).count(),
            1,
        )