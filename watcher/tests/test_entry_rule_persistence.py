from datetime import date

from django.test import TestCase

from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.knowledge_base.models import Filing


class EntryRulePersistenceTests(TestCase):

    def setUp(self):
        self.service = FilingRegistrationService()

        self.base_args = {
            "ticker": "TEST",
            "cik": "0000000001",
            "company_name": "Test Company",
            "form": "8-K",
            "accession_number": "0000000001-26-000001",
            "sequence": 1,
            "filing_date": date(2026, 9, 14),
            "primary_document": "test.htm",
            "local_path": "downloads/test.htm",
            "source_url": "https://example.com/test.htm",
        }

    def test_entry_rule_is_persisted_with_entry_session(self):
        filing = self.service.register(
            **self.base_args,
            entry_session=date(2026, 9, 15),
            entry_rule="T_PLUS_1",
        )

        filing.refresh_from_db()

        self.assertEqual(
            filing.entry_session,
            date(2026, 9, 15),
        )
        self.assertEqual(
            filing.entry_rule,
            "T_PLUS_1",
        )

    def test_registration_without_entry_session_keeps_rule_null(self):
        filing = self.service.register(
            **self.base_args
        )

        filing.refresh_from_db()

        self.assertIsNone(filing.entry_session)
        self.assertIsNone(filing.entry_rule)

    def test_entry_session_requires_entry_rule(self):
        with self.assertRaisesRegex(
            ValueError,
            "entry_rule is required",
        ):
            self.service.register(
                **self.base_args,
                entry_session=date(2026, 9, 15),
            )

    def test_invalid_entry_rule_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "Unsupported entry_rule",
        ):
            self.service.register(
                **self.base_args,
                entry_session=date(2026, 9, 15),
                entry_rule="BAD_RULE",
            )

    def test_entry_rule_is_normalized(self):
        filing = self.service.register(
            **self.base_args,
            entry_session=date(2026, 9, 15),
            entry_rule="t_plus_1",
        )

        filing.refresh_from_db()

        self.assertEqual(
            filing.entry_rule,
            "T_PLUS_1",
        )

    def test_rule_change_requires_full_restamp(self):
        self.service.register(
            **self.base_args,
            entry_session=date(2026, 9, 15),
            entry_rule="T_PLUS_1",
        )

        with self.assertRaisesRegex(
            ValueError,
            "full entry-session re-stamp",
        ):
            self.service.register(
                **self.base_args,
                entry_session=date(2026, 9, 14),
                entry_rule="SAME_SESSION",
            )

        filing = Filing.objects.get(
            accession_number=(
                self.base_args["accession_number"]
            )
        )

        self.assertEqual(
            filing.entry_rule,
            "T_PLUS_1",
        )
        self.assertEqual(
            filing.entry_session,
            date(2026, 9, 15),
        )