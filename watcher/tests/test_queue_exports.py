"""Capture queue exports API: date-range listing and download.

Covers the range filter, the listing shape, the download, the
path-traversal defences, permissions, and read-only behaviour.
"""

import shutil
import tempfile

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

LIST_URL = "/api/queue/exports/"

MARKET_TZ = ZoneInfo("America/New_York")

HEADER = (
    "ticker,cik,accession,accepted_ts,filing_date,"
    "entry_session,items,url,flag,entry_rule\n"
)

ROW = (
    "MET,0001099219,0001099219-26-000055,"
    "2026-09-24T12:06:25-04:00,2026-09-24,2026-09-25,"
    "7.01,https://example.com,false,T_PLUS_1\n"
)


class QueueExportsTests(TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.queue = self.tmp / "queue"
        self.queue.mkdir()

        self.write("2026-09-22.csv", rows=2)
        self.write("2026-09-23.csv", rows=3)
        self.write("2026-09-23.r2.csv", rows=4)
        self.write("2026-09-24.csv", rows=1)

        # Must never be listed.
        (self.queue / "notes.txt").write_text("ignore me")
        (self.queue / "2026-09-24.csv.tmp").write_text("partial")

        User = get_user_model()
        self.admin = User.objects.create_user(
            username="qx-admin",
            password="pw-qx-admin-123",
            is_staff=True,
        )
        self.plain = User.objects.create_user(
            username="qx-user",
            password="pw-qx-user-123",
        )

        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def write(self, name, rows=1):
        (self.queue / name).write_text(HEADER + ROW * rows)

    def listing(self, params=None, client=None):
        with override_settings(BASE_DIR=self.tmp):
            return (client or self.client).get(LIST_URL, params or {})

    def download(self, filename, client=None):
        with override_settings(BASE_DIR=self.tmp):
            return (client or self.client).get(
                f"{LIST_URL}{filename}/"
            )

    # ------------------------------------------------------------------
    # Range filtering
    # ------------------------------------------------------------------

    def test_range_returns_only_files_inside_it(self):
        data = self.listing(
            {"start": "2026-09-23", "end": "2026-09-24"}
        ).json()

        self.assertEqual(
            [f["filename"] for f in data["files"]],
            [
                "2026-09-24.csv",
                "2026-09-23.r2.csv",
                "2026-09-23.csv",
            ],
        )

    def test_bounds_are_inclusive(self):
        data = self.listing(
            {"start": "2026-09-22", "end": "2026-09-22"}
        ).json()

        self.assertEqual(
            [f["filename"] for f in data["files"]],
            ["2026-09-22.csv"],
        )

    def test_empty_range_is_not_an_error(self):
        response = self.listing(
            {"start": "2020-01-01", "end": "2020-01-31"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["files"], [])

    def test_default_range_is_returned_when_no_dates_given(self):
        data = self.listing().json()

        today = datetime.now(MARKET_TZ).date()

        self.assertEqual(data["end"], today.isoformat())
        self.assertEqual(
            data["start"],
            (today - timedelta(days=29)).isoformat(),
        )

    def test_total_rows_is_summed(self):
        data = self.listing(
            {"start": "2026-09-22", "end": "2026-09-24"}
        ).json()

        # 1 + 4 + 3 + 2
        self.assertEqual(data["total_rows"], 10)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def test_malformed_date_is_rejected(self):
        self.assertEqual(
            self.listing({"start": "24-09-2026"}).status_code,
            400,
        )

    def test_start_after_end_is_rejected(self):
        self.assertEqual(
            self.listing(
                {"start": "2026-09-24", "end": "2026-09-22"}
            ).status_code,
            400,
        )

    def test_absurdly_wide_range_is_rejected(self):
        self.assertEqual(
            self.listing(
                {"start": "2000-01-01", "end": "2026-09-24"}
            ).status_code,
            400,
        )

    # ------------------------------------------------------------------
    # Listing shape
    # ------------------------------------------------------------------

    def test_non_queue_files_are_ignored(self):
        names = [
            f["filename"]
            for f in self.listing(
                {"start": "2026-09-01", "end": "2026-09-30"}
            ).json()["files"]
        ]

        self.assertNotIn("notes.txt", names)
        self.assertNotIn("2026-09-24.csv.tmp", names)

    def test_rows_exclude_the_header_and_revision_is_reported(self):
        by_name = {
            f["filename"]: f
            for f in self.listing(
                {"start": "2026-09-23", "end": "2026-09-23"}
            ).json()["files"]
        }

        self.assertEqual(by_name["2026-09-23.csv"]["rows"], 3)
        self.assertEqual(by_name["2026-09-23.csv"]["revision"], 1)
        self.assertEqual(by_name["2026-09-23.r2.csv"]["rows"], 4)
        self.assertEqual(by_name["2026-09-23.r2.csv"]["revision"], 2)

    def test_missing_queue_directory_is_not_an_error(self):
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, True)

        with override_settings(BASE_DIR=empty):
            response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["files"], [])

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def test_download_returns_the_csv_as_an_attachment(self):
        response = self.download("2026-09-23.csv")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("2026-09-23.csv", response["Content-Disposition"])

        body = b"".join(response.streaming_content).decode()

        self.assertTrue(body.startswith("ticker,cik,accession"))
        self.assertEqual(body.count("MET"), 3)

    def test_unknown_file_is_404(self):
        self.assertEqual(
            self.download("2020-01-01.csv").status_code,
            404,
        )

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    def test_path_traversal_is_rejected(self):
        for attack in (
            "..%2F..%2Fconfig%2Fsettings.py",
            "....//settings.py",
            "2026-09-24.csv.tmp",
            "notes.txt",
            "manage.py",
        ):
            with self.subTest(attack=attack):
                self.assertEqual(
                    self.download(attack).status_code,
                    404,
                )

    def test_non_admin_is_forbidden(self):
        client = APIClient()
        client.force_authenticate(self.plain)

        self.assertEqual(self.listing(client=client).status_code, 403)
        self.assertEqual(
            self.download("2026-09-23.csv", client=client).status_code,
            403,
        )

    def test_anonymous_is_rejected(self):
        self.assertEqual(
            self.listing(client=APIClient()).status_code,
            401,
        )

    def test_endpoint_never_writes(self):
        before = sorted(
            (p.name, p.stat().st_size)
            for p in self.queue.iterdir()
        )

        self.listing({"start": "2026-09-22", "end": "2026-09-24"})
        self.download("2026-09-23.csv")

        self.assertEqual(
            sorted(
                (p.name, p.stat().st_size)
                for p in self.queue.iterdir()
            ),
            before,
        )