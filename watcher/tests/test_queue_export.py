"""
W-038: queue file export (R-07, R-13, RM-01).

Covers the schema, the append-only guarantee, revision behaviour, and
the fact that the export never mutates anything.
"""

import csv
import shutil
import tempfile

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from watcher.models import Company, Filing
from watcher.management.commands.export_queue import (
    QUEUE_COLUMNS,
    previous_market_day,
    resolve_output_path,
)

MARKET_TZ = ZoneInfo("America/New_York")


class QueueExportTestCase(TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

        self.company = Company.objects.create(
            ticker="AAPL",
            cik="0000320193",
            name="Apple Inc.",
        )

        self.day = date(2026, 9, 23)

        self.filing = Filing.objects.create(
            company=self.company,
            accession_number="0000320193-26-000101",
            sequence=1,
            form="8-K",
            filing_date=self.day,
            accepted_at=datetime(
                2026, 9, 23, 16, 5, 0, tzinfo=MARKET_TZ,
            ),
            entry_session=date(2026, 9, 24),
            entry_rule="T_PLUS_1",
            sec_item_codes="2.02;9.01",
            source_url="https://www.sec.gov/Archives/example.htm",
            local_path="/tmp/example.htm",
            flag=False,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def queue_dir(self):
        return self.tmp / "queue"

    def export(self, **kwargs):
        with override_settings(BASE_DIR=self.tmp):
            call_command(
                "export_queue",
                date=self.day.isoformat(),
                **kwargs,
            )

    def read(self, name):
        with open(self.queue_dir() / name, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def test_columns_match_r13_plus_entry_rule(self):
        self.export()

        with open(
            self.queue_dir() / f"{self.day.isoformat()}.csv",
            newline="",
            encoding="utf-8",
        ) as fh:
            header = next(csv.reader(fh))

        self.assertEqual(header, list(QUEUE_COLUMNS))
        self.assertEqual(len(header), 10)

    def test_row_contents(self):
        self.export()

        rows = self.read(f"{self.day.isoformat()}.csv")

        self.assertEqual(len(rows), 1)

        row = rows[0]

        self.assertEqual(row["ticker"], "AAPL")
        self.assertEqual(row["cik"], "0000320193")
        self.assertEqual(row["accession"], "0000320193-26-000101")
        self.assertEqual(row["filing_date"], "2026-09-23")
        self.assertEqual(row["entry_session"], "2026-09-24")
        self.assertEqual(row["items"], "2.02;9.01")
        self.assertEqual(row["flag"], "false")
        self.assertEqual(row["entry_rule"], "T_PLUS_1")
        self.assertTrue(row["accepted_ts"].startswith("2026-09-23T16:05"))

    def test_flagged_row_is_marked(self):
        self.filing.flag = True
        self.filing.save()

        self.export()

        self.assertEqual(
            self.read(f"{self.day.isoformat()}.csv")[0]["flag"],
            "true",
        )

    def test_row_with_no_acceptance_timestamp_still_exports(self):
        # R-12 flags rather than guessing. Such a row must not vanish
        # from the queue; it falls back to filing_date for day
        # selection and carries an empty accepted_ts.
        self.filing.accepted_at = None
        self.filing.flag = True
        self.filing.save()

        self.export()

        row = self.read(f"{self.day.isoformat()}.csv")[0]

        self.assertEqual(row["accepted_ts"], "")
        self.assertEqual(row["flag"], "true")

    # ------------------------------------------------------------------
    # Append-only / revisions
    # ------------------------------------------------------------------

    def test_existing_file_is_never_overwritten(self):
        self.export()

        original = self.read(f"{self.day.isoformat()}.csv")

        # Change the row, then re-export.
        self.filing.entry_session = date(2026, 9, 25)
        self.filing.save()

        self.export()

        # Revision written, original untouched.
        self.assertEqual(
            self.read(f"{self.day.isoformat()}.csv"),
            original,
        )

        revised = self.read(f"{self.day.isoformat()}.r2.csv")

        self.assertEqual(revised[0]["entry_session"], "2026-09-25")

    def test_unchanged_reexport_writes_nothing(self):
        # A nightly sweep re-exports days it already exported. Writing
        # a revision every night would make revisions meaningless.
        self.export()
        self.export()

        files = sorted(p.name for p in self.queue_dir().glob("*.csv"))

        self.assertEqual(files, [f"{self.day.isoformat()}.csv"])

    def test_force_writes_a_revision_even_when_unchanged(self):
        self.export()
        self.export(force=True)

        self.assertTrue(
            (self.queue_dir() / f"{self.day.isoformat()}.r2.csv").exists()
        )

    def test_resolve_output_path_increments(self):
        queue_dir = self.queue_dir()
        queue_dir.mkdir(parents=True)

        path, rev = resolve_output_path(queue_dir, self.day)
        self.assertEqual(rev, 1)
        path.write_text("x")

        path, rev = resolve_output_path(queue_dir, self.day)
        self.assertEqual(rev, 2)
        self.assertTrue(path.name.endswith(".r2.csv"))

    def test_no_temp_file_is_left_behind(self):
        self.export()

        self.assertEqual(
            list(self.queue_dir().glob("*.tmp")),
            [],
        )

    # ------------------------------------------------------------------
    # Scope and safety
    # ------------------------------------------------------------------

    def test_ten_k_excluded_by_default_and_included_with_all_forms(self):
        Filing.objects.create(
            company=self.company,
            accession_number="0000320193-26-000102",
            sequence=1,
            form="10-K",
            filing_date=self.day,
            accepted_at=datetime(
                2026, 9, 23, 16, 30, 0, tzinfo=MARKET_TZ,
            ),
            local_path="/tmp/tenk.htm",
        )

        self.export()
        self.assertEqual(len(self.read(f"{self.day.isoformat()}.csv")), 1)

        self.export(all_forms=True, force=True)
        self.assertEqual(len(self.read(f"{self.day.isoformat()}.r2.csv")), 2)

    def test_other_days_are_not_included(self):
        Filing.objects.create(
            company=self.company,
            accession_number="0000320193-26-000103",
            sequence=1,
            form="8-K",
            filing_date=self.day - timedelta(days=1),
            accepted_at=datetime(
                2026, 9, 22, 10, 0, 0, tzinfo=MARKET_TZ,
            ),
            local_path="/tmp/other.htm",
        )

        self.export()

        self.assertEqual(len(self.read(f"{self.day.isoformat()}.csv")), 1)

    def test_empty_day_writes_nothing_by_default(self):
        Filing.objects.all().delete()

        self.export()

        self.assertFalse(self.queue_dir().exists() and list(self.queue_dir().glob("*.csv")))

    def test_empty_day_with_empty_ok_writes_header_only(self):
        Filing.objects.all().delete()

        self.export(empty_ok=True)

        self.assertEqual(self.read(f"{self.day.isoformat()}.csv"), [])

    def test_dry_run_writes_nothing(self):
        self.export(dry_run=True)

        self.assertFalse(
            (self.queue_dir() / f"{self.day.isoformat()}.csv").exists()
        )

    def test_export_does_not_mutate_filings(self):
        before = Filing.objects.get(pk=self.filing.pk)
        before_updated = before.updated_at

        self.export()

        after = Filing.objects.get(pk=self.filing.pk)

        self.assertEqual(after.updated_at, before_updated)
        self.assertEqual(after.entry_session, before.entry_session)
        self.assertEqual(after.flag, before.flag)

    def test_invalid_date_is_rejected(self):
        with override_settings(BASE_DIR=self.tmp):
            with self.assertRaises(CommandError):
                call_command("export_queue", date="not-a-date")

    def test_previous_market_day_skips_the_weekend(self):
        # Monday 2026-09-28 -> Friday 2026-09-25
        self.assertEqual(
            previous_market_day(date(2026, 9, 28)),
            date(2026, 9, 25),
        )