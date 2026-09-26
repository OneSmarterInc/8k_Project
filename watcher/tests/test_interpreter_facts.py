"""Prompt 1.0.2 facts: amounts in any currency, event date, counterparty."""

import json

from django.test import TestCase

from watcher.interpreter import parser as P
from watcher.interpreter.facts import clean_facts, event_date, format_amounts
from watcher.interpreter.pipeline import email_block
from watcher.interpreter.service import InterpreterService
from watcher.services.notification_service import FilingNotificationService

from .test_interpreter import FakeGenerator, register


# What 1.0.2 should return for AIG 0001104659-26-110438.
AIG_ANSWER = json.dumps({
    "is_material": True,
    "category": "FINANCING",
    "confidence": 0.85,
    "body_item_numbers": ["8.01", "9.01"],
    "extracted_facts": {
        "counterparty": None,
        "amounts": [
            {"value": 625000000, "currency": "eur", "description": "4.250% Notes due 2031"},
            {"value": 500000000, "currency": "EUR", "description": "4.750% Notes due 2036"},
        ],
        "event_date": "2026-09-24",
    },
    "reasoning": "Closed a euro notes offering.",
})


class CleanFactsTests(TestCase):

    def test_keeps_valid_values(self):
        facts = clean_facts({
            "counterparty": "  First National Bank ",
            "amounts": [{"value": 350000000, "currency": "usd"}],
            "event_date": "2026-09-14",
        })
        self.assertEqual(facts["counterparty"], "First National Bank")
        self.assertEqual(facts["amounts"], [
            {"value": 350000000, "currency": "USD", "description": None}
        ])
        self.assertEqual(facts["event_date"], "2026-09-14")

    def test_drops_malformed_values_never_invents(self):
        facts = clean_facts({
            "counterparty": "",
            "amounts": [
                {"value": "625 million", "currency": "EUR"},   # text, dropped
                {"value": True, "currency": "EUR"},            # bool, dropped
                {"value": 5, "currency": "EURO"},              # bad code -> None
                "garbage",
            ],
            "event_date": "September 24, 2026",               # not ISO -> None
        })
        self.assertIsNone(facts["counterparty"])
        self.assertEqual(facts["amounts"], [{"value": 5, "currency": None, "description": None}])
        self.assertIsNone(facts["event_date"])

    def test_old_shape_passes_through(self):
        facts = clean_facts({"amount_usd": 350000000, "effective_date": "2026-09-14"})
        self.assertEqual(format_amounts(facts), "$350,000,000")
        self.assertEqual(event_date(facts), "2026-09-14")

    def test_empty(self):
        self.assertEqual(clean_facts(None), {})
        self.assertIsNone(format_amounts({"amounts": []}))
        self.assertIsNone(event_date({}))


class AigCaseTests(TestCase):

    def test_parser_keeps_both_euro_tranches(self):
        data, code = P.parse_response(AIG_ANSWER)
        self.assertIsNone(code)
        self.assertEqual(
            format_amounts(data["extracted_facts"]),
            "EUR 625,000,000 (4.250% Notes due 2031); "
            "EUR 500,000,000 (4.750% Notes due 2036)",
        )

    def test_end_to_end_into_email(self):
        row = InterpreterService(
            generator=FakeGenerator(AIG_ANSWER), threshold=0.7
        ).classify(register(1))
        block = email_block(row)
        self.assertIn("EUR 625,000,000", block["amount_usd"])
        self.assertEqual(block["effective_date"], "2026-09-24")

        text = FilingNotificationService._build_body(
            ticker="AIG", form_type="8-K", filename="d", filing_date="2026-09-24",
            accession_number="a", local_path="/t", sec_url="u",
            clean_summary="Summary.", accepted_at_display="", entry_session=None,
            item_codes=(), item_verification_status="",
            company_verification_status="", manual_audit_status="",
            interpretation=block,
        )
        self.assertIn("Amount: EUR 625,000,000 (4.250% Notes due 2031)", text)
        self.assertIn("Event Date: 2026-09-24", text)
        self.assertIn("Counterparty: N/A", text)
