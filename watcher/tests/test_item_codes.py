"""W-036: item codes are stored as "1.01;9.01", never as str(tuple)."""

from importlib import import_module

from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase

from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.models import Company, Filing
from watcher.services.item_codes import format_item_codes, parse_item_codes


class ItemCodeFormatTests(SimpleTestCase):

    def test_tuple_is_joined_with_semicolons(self):
        self.assertEqual(format_item_codes(("1.01", "9.01")), "1.01;9.01")

    def test_comma_string_from_sec_is_normalized(self):
        self.assertEqual(format_item_codes("1.01, 9.01"), "1.01;9.01")

    def test_legacy_tuple_text_is_repaired(self):
        self.assertEqual(format_item_codes("('1.01', '9.01')"), "1.01;9.01")

    def test_single_item_legacy_tuple_text(self):
        self.assertEqual(format_item_codes("('7.01',)"), "7.01")

    def test_empty_inputs(self):
        for value in (None, "", (), [], "()"):
            self.assertEqual(format_item_codes(value), "")

    def test_duplicates_removed_order_kept(self):
        self.assertEqual(format_item_codes(["9.01", "1.01", "9.01"]), "9.01;1.01")

    def test_nothing_is_dropped_or_invented(self):
        self.assertEqual(parse_item_codes("2.02;X.99"), ("2.02", "X.99"))

    def test_already_canonical_is_unchanged(self):
        self.assertEqual(format_item_codes("1.01;9.01"), "1.01;9.01")


class ItemCodeRegistrationTests(TestCase):

    def _register(self, accession, **extra):
        return FilingRegistrationService().register(
            ticker="TEST", cik="1234567890", company_name="Test Co",
            form="8-K", accession_number=accession, sequence=1,
            filing_date="2026-01-05", primary_document="d.htm",
            local_path=f"/tmp/{accession}", source_url=f"http://t/{accession}",
            report_date="2026-01-02", **extra,
        )

    def test_new_filing_stores_canonical_codes(self):
        filing = self._register(
            "A1", sec_item_codes=("1.01", "9.01"), parsed_item_codes=("1.01",)
        )
        filing.refresh_from_db()
        self.assertEqual(filing.sec_item_codes, "1.01;9.01")
        self.assertEqual(filing.parsed_item_codes, "1.01")

    def test_existing_filing_update_stores_canonical_codes(self):
        self._register("A2")
        filing = self._register("A2", sec_item_codes=("2.02", "9.01"))
        filing.refresh_from_db()
        self.assertEqual(filing.sec_item_codes, "2.02;9.01")


class ItemCodeDataMigrationTests(TestCase):

    def test_migration_repairs_legacy_rows(self):
        company = Company.objects.create(ticker="OLD", cik="999")
        filing = Filing.objects.create(
            company=company, accession_number="OLD1", form="8-K",
            local_path="/tmp/old",
        )
        Filing.objects.filter(pk=filing.pk).update(
            sec_item_codes="('1.01', '9.01')",
            parsed_item_codes="('1.01',)",
        )

        migration = import_module("watcher.migrations.0022_normalize_item_codes")
        migration.normalize_item_codes(django_apps, None)

        filing.refresh_from_db()
        self.assertEqual(filing.sec_item_codes, "1.01;9.01")
        self.assertEqual(filing.parsed_item_codes, "1.01")