import csv
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.management.commands.reconcile_daily_index import (
    parse_master_index,
)

INDEX_TEXT = """Description:           Daily Index of EDGAR Dissemination Feed by Company Name
Last Data Received:    Sep 23, 2026

CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
1111111111|ALPHA CORP|8-K|20260923|edgar/data/1111111111/0001111111-26-000001.txt
1111111111|ALPHA CORP|8-K/A|20260923|edgar/data/1111111111/0001111111-26-000002.txt
2222222222|BETA INC|8-K|20260923|edgar/data/2222222222/0002222222-26-000009.txt
2222222222|BETA INC|10-Q|20260923|edgar/data/2222222222/0002222222-26-000010.txt
9999999999|NOT IN UNIVERSE|8-K|20260923|edgar/data/9999999999/0009999999-26-000001.txt
"""


class ParseMasterIndexTests(TestCase):
    def test_parses_only_8k_rows(self):
        rows = parse_master_index(INDEX_TEXT)
        self.assertEqual(
            [r["accession"] for r in rows],
            [
                "0001111111-26-000001",
                "0001111111-26-000002",
                "0002222222-26-000009",
                "0009999999-26-000001",
            ],
        )
        self.assertEqual(rows[0]["cik"], 1111111111)


class ReconcileCommandTests(TestCase):
    def _register(self, ticker, cik, accession, form="8-K", filing_date="2026-09-23"):
        return FilingRegistrationService().register(
            ticker=ticker,
            cik=cik,
            company_name=ticker,
            form=form,
            accession_number=accession,
            sequence=1,
            filing_date=filing_date,
            primary_document="d.htm",
            local_path="/tmp/d",
            source_url="http://test/d",
            report_date=filing_date,
        )

    def _run(self, status_code=200, text=INDEX_TEXT):
        response = MagicMock(status_code=status_code, text=text)
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "watcher.management.commands.reconcile_daily_index.SECClient"
        ) as client_cls:
            client_cls.return_value.get.return_value = response
            call_command(
                "reconcile_daily_index",
                "--date", "2026-09-23",
                "--out-dir", tmp,
                stdout=out,
            )
            summary = list(csv.DictReader(
                (Path(tmp) / "reconciliation_20260923_20260923.csv").open()
            ))
            gaps = list(csv.DictReader(
                (Path(tmp) / "gaps_20260923_20260923.csv").open()
            ))
        return out.getvalue(), summary, gaps

    def test_reports_missing_and_extra(self):
        self._register("ALPHA", "1111111111", "0001111111-26-000001")
        self._register("ALPHA", "1111111111", "0001111111-26-000002", form="8-K/A")
        self._register("BETA", "2222222222", "0002222222-26-000777")  # not in index

        _, summary, gaps = self._run()

        self.assertEqual(summary[0]["edgar_count"], "3")   # universe 8-K/8-K/A only
        self.assertEqual(summary[0]["captured"], "2")
        self.assertEqual(summary[0]["missing"], "1")
        self.assertEqual(summary[0]["extra"], "1")
        self.assertEqual(summary[0]["result"], "GAP")
        self.assertEqual(
            {(g["type"], g["accession"]) for g in gaps},
            {
                ("MISSING", "0002222222-26-000009"),
                ("EXTRA", "0002222222-26-000777"),
            },
        )

    def test_full_match(self):
        self._register("ALPHA", "1111111111", "0001111111-26-000001")
        self._register("ALPHA", "1111111111", "0001111111-26-000002", form="8-K/A")
        self._register("BETA", "2222222222", "0002222222-26-000009")

        _, summary, gaps = self._run()
        self.assertEqual(summary[0]["result"], "MATCH")
        self.assertEqual(gaps, [])

    def test_missing_index_is_reported_not_crashed(self):
        self._register("ALPHA", "1111111111", "0001111111-26-000001")
        _, summary, _ = self._run(status_code=404, text="")
        self.assertEqual(summary[0]["edgar_index"], "NOT AVAILABLE")

    def test_command_does_not_modify_filings(self):
        filing = self._register("ALPHA", "1111111111", "0001111111-26-000001")
        before = filing.__class__.objects.count()
        self._run()
        self.assertEqual(filing.__class__.objects.count(), before)