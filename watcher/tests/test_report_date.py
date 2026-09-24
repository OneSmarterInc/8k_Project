"""W-037: a missing or malformed reportDate must not lose the filing."""

import datetime

from django.test import SimpleTestCase, TestCase

from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.services.filing_discovery import FilingDiscovery


def _register(accession, report_date, form="8-K"):
    return FilingRegistrationService().register(
        ticker="TEST", cik="1234567890", company_name="Test Co",
        form=form, accession_number=accession, sequence=1,
        filing_date="2026-01-05", primary_document="d.htm",
        local_path=f"/tmp/{accession}", source_url=f"http://t/{accession}",
        report_date=report_date,
    )


class ReportDateRegistrationTests(TestCase):

    def test_blank_report_date_registers_with_null(self):
        filing = _register("B1", "")
        filing.refresh_from_db()
        self.assertIsNone(filing.report_date)

    def test_whitespace_report_date_registers_with_null(self):
        filing = _register("B2", "   ")
        filing.refresh_from_db()
        self.assertIsNone(filing.report_date)

    def test_malformed_report_date_registers_with_null(self):
        filing = _register("B3", "2026-13-45")
        filing.refresh_from_db()
        self.assertIsNone(filing.report_date)

    def test_valid_string_is_stored_as_date(self):
        filing = _register("B4", "2026-01-02")
        filing.refresh_from_db()
        self.assertEqual(filing.report_date, datetime.date(2026, 1, 2))

    def test_date_object_is_accepted(self):
        filing = _register("B5", datetime.date(2026, 1, 2))
        filing.refresh_from_db()
        self.assertEqual(filing.report_date, datetime.date(2026, 1, 2))

    def test_amendment_with_blank_report_date_stays_unlinked(self):
        _register("ORIG", "2026-01-02")
        amendment = _register("AMD", "", form="8-K/A")
        amendment.refresh_from_db()
        self.assertIsNone(amendment.amends)
        self.assertIsNone(amendment.report_date)


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url):
        return self.payload


class ReportDateDiscoveryTests(SimpleTestCase):

    def test_discovery_returns_none_for_blank_report_date(self):
        today = datetime.date.today().isoformat()
        payload = {
            "cik": "1234567890", "name": "Test Co", "tickers": ["TEST"],
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": [today],
                    "reportDate": [""],
                    "accessionNumber": ["0000000000-26-000001"],
                    "primaryDocument": ["d.htm"],
                    "primaryDocDescription": [""],
                    "acceptanceDateTime": [f"{today}T16:30:00.000Z"],
                    "items": ["8.01"],
                },
                "files": [],
            },
        }
        result = FilingDiscovery(client=_FakeClient(payload)).list_filings(
            cik="1234567890", form="8-K",
            start_date=datetime.date.today() - datetime.timedelta(days=1),
            end_date=datetime.date.today() + datetime.timedelta(days=1),
        )
        self.assertEqual(len(result["filings"]), 1)
        self.assertIsNone(result["filings"][0]["report_date"])