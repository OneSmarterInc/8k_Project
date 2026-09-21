from django.test import TestCase
from watcher.knowledge_base.models import Company, Filing
from watcher.knowledge_base.ingestion.filing_registration import FilingRegistrationService
from django.utils import timezone

class FilingAmendsTests(TestCase):
    def test_multiple_amendments_to_same_original(self):
        service = FilingRegistrationService()
        
        company = Company.objects.create(ticker="TEST", cik="1234567890", name="Test Co")
        
        # Original filing
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
        
        # First amendment
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
        
        # Second amendment
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
        
        # Both amendments should point to the original filing
        self.assertEqual(amendment1.amends, original)
        self.assertEqual(amendment2.amends, original)
        
        # We can fetch them via the related name
        amended_by = list(original.amended_by.all())
        self.assertEqual(len(amended_by), 2)
        self.assertIn(amendment1, amended_by)
        self.assertIn(amendment2, amended_by)
