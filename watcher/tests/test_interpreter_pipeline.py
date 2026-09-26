"""
Interpreter step inside the Watcher pipeline (INTERPRETER_IN_PIPELINE).

Proves: off = identical behaviour; on = only the new filing is
classified and its result goes into the email; a failure records a
FailureEvent (Review Queue -> Failures), the email still goes out, and a
later successful `interpret` resolves the failure.
"""

from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from watcher.interpreter.pipeline import (
    PipelineInterpreterStep,
    default_step,
    pipeline_enabled,
)
from watcher.interpreter.service import InterpreterService
from watcher.knowledge_base.models import FailureEvent
from watcher.models import FilingClassification
from watcher.services.filing_email_service import FilingEmailService
from watcher.services.filing_pipeline.post_processing import (
    FilingPostProcessingService,
)
from watcher.services.notification_service import FilingNotificationService

from .test_interpreter import FakeGenerator, answer, register


# These tests never call SEC; exhibit fetching has its own tests.
@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class PipelineStepTests(TestCase):

    def setUp(self):
        self.filing = register(1)
        self.other = register(2)          # an older filing, must not be touched

    def _run(self, *, step, form="8-K"):
        output = MagicMock()
        email_service = MagicMock()
        email_service.send.return_value = True
        metadata_service = MagicMock()
        metadata_service.verify_items.return_value = (None, None)

        kwargs = dict(
            auto_index=True,
            daily_chronicle=True,
            indexing_service=MagicMock(),
            summary_service=MagicMock(),
            metadata_service=metadata_service,
            email_service=email_service,
            notification_service=MagicMock(recipient=""),
            output_service=output,
        )
        if step != "default":
            kwargs["interpreter_step"] = step

        result = FilingPostProcessingService(**kwargs).run(
            registered_filing=self.filing,
            form=form,
            accession_number=self.filing.accession_number,
            metadata=MagicMock(sec_item_codes=""),
            filename="d.htm",
            saved_path="/tmp/d",
            source_url="http://test/d",
        )
        statuses = [c.args for c in output.status.call_args_list]
        return statuses, email_service, result

    def _step(self, reply=None, error=None):
        return PipelineInterpreterStep(
            service=InterpreterService(
                generator=FakeGenerator(reply=reply, error=error),
                threshold=0.7,
            )
        )

    # ---- switch -----------------------------------------------------

    @override_settings(INTERPRETER_IN_PIPELINE=False)
    def test_switch_off_by_default(self):
        self.assertFalse(pipeline_enabled())
        self.assertIsNone(default_step())

    @override_settings(INTERPRETER_IN_PIPELINE=True)
    def test_switch_on_from_settings(self):
        self.assertTrue(pipeline_enabled())
        self.assertIsInstance(default_step(), PipelineInterpreterStep)

    @override_settings(INTERPRETER_IN_PIPELINE=False)
    def test_off_means_identical_pipeline(self):
        statuses, email, _ = self._run(step="default")
        self.assertFalse(any(s[0] == "INTERPRETER" for s in statuses))
        self.assertNotIn("interpretation", email.send.call_args.kwargs)
        self.assertEqual(
            set(email.send.call_args.kwargs),
            {"summary_result", "filename", "saved_path", "source_url",
             "metadata", "item_verification"},
        )
        self.assertEqual(FilingClassification.objects.count(), 0)

    # ---- success ----------------------------------------------------

    def test_on_classifies_only_the_new_filing_and_emails_result(self):
        statuses, email, _ = self._run(step=self._step())

        self.assertIn(("INTERPRETER", "FINANCING 0.90"), statuses)
        self.assertEqual(
            list(FilingClassification.objects.values_list("filing_id", flat=True)),
            [self.filing.id],
        )
        block = email.send.call_args.kwargs["interpretation"]
        self.assertEqual(block["category"], "FINANCING")
        self.assertEqual(block["status"], "Confident")
        self.assertIn(("EMAIL", "SENT SUCCESSFULLY"), statuses)

    def test_low_confidence_marked_for_review_in_email(self):
        statuses, email, _ = self._run(step=self._step(answer(confidence=0.4)))
        self.assertIn(("INTERPRETER", "FINANCING 0.40 -> REVIEW"), statuses)
        self.assertIn(
            "Review Queue",
            email.send.call_args.kwargs["interpretation"]["status"],
        )

    def test_non_8k_is_left_alone(self):
        statuses, email, _ = self._run(step=self._step(), form="10-Q")
        self.assertFalse(any(s[0] == "INTERPRETER" for s in statuses))
        self.assertNotIn("interpretation", email.send.call_args.kwargs)

    # ---- failure ----------------------------------------------------

    def test_failure_records_failureevent_email_still_sent(self):
        statuses, email, result = self._run(step=self._step(error="connection refused"))

        self.assertTrue(any(
            s[0] == "INTERPRETER" and s[1].startswith("FAILED") for s in statuses
        ))
        event = FailureEvent.objects.get(filing=self.filing)
        self.assertEqual(event.stage, FailureEvent.Stage.INTERPRETER)
        self.assertEqual(event.code, FailureEvent.Code.INTERPRETER_FAILED)
        self.assertIsNone(event.resolved_at)

        self.assertIn(("EMAIL", "SENT SUCCESSFULLY"), statuses)
        self.assertIn("Failures", email.send.call_args.kwargs["interpretation"]["status"])
        self.assertEqual(result["indexed"] + result.get("index_failed", 0), result["indexed"])
        self.assertTrue(result["errors"])     # printed as a warning only

    def test_unexpected_crash_is_contained(self):
        step = MagicMock()
        step.applies_to.return_value = True
        step.classify.side_effect = RuntimeError("boom")
        statuses, email, _ = self._run(step=step)
        self.assertIn(("EMAIL", "SENT SUCCESSFULLY"), statuses)
        self.assertEqual(FailureEvent.objects.filter(filing=self.filing).count(), 1)

    def test_later_success_resolves_the_failure(self):
        self._run(step=self._step(error="down"))
        InterpreterService(generator=FakeGenerator(), threshold=0.7).classify(self.filing)
        event = FailureEvent.objects.get(filing=self.filing)
        self.assertIsNotNone(event.resolved_at)

    def test_failed_filing_goes_to_failures_tab_then_back(self):
        from django.contrib.auth import get_user_model
        client = APIClient()
        client.force_authenticate(
            get_user_model().objects.create_user("s", password="pw-s-12345", is_staff=True)
        )
        self._run(step=self._step(error="down"))

        ids = lambda p: {r["id"] for r in client.get(reverse("filings"), p).json()["results"]}
        self.assertIn(self.filing.id, ids({"status": "review"}))
        self.assertNotIn(self.filing.id, ids({}))

        InterpreterService(generator=FakeGenerator(), threshold=0.7).classify(self.filing)
        self.assertNotIn(self.filing.id, ids({"status": "review"}))
        self.assertIn(self.filing.id, ids({}))


# These tests never call SEC; exhibit fetching has its own tests.
@override_settings(INTERPRETER_FETCH_EXHIBITS=False)
class EmailContentTests(TestCase):
    """The real email builders, not mocks."""

    BLOCK = {
        "material": "Yes", "category": "FINANCING", "confidence": "0.85",
        "counterparty": "First <Bank>", "amount_usd": 625000000,
        "effective_date": None, "status": "Confident",
    }

    def _kwargs(self, **extra):
        base = dict(
            ticker="AIG", form_type="8-K", filename="d.htm",
            filing_date="2026-09-24", accession_number="0001", local_path="/t",
            sec_url="http://sec", clean_summary="Summary text.",
            accepted_at_display="", entry_session=None, item_codes=("8.01",),
            item_verification_status="", company_verification_status="",
            manual_audit_status="",
        )
        base.update(extra)
        return base

    def test_no_block_means_identical_email(self):
        text_old = FilingNotificationService._build_body(**self._kwargs())
        text_new = FilingNotificationService._build_body(**self._kwargs(interpretation=None))
        self.assertEqual(text_old, text_new)
        html_old = FilingNotificationService._build_html_body(**self._kwargs())
        html_new = FilingNotificationService._build_html_body(**self._kwargs(interpretation=None))
        self.assertEqual(html_old, html_new)
        self.assertNotIn("INTERPRETER", text_old)

    def test_block_in_text_and_html_and_escaped(self):
        text = FilingNotificationService._build_body(**self._kwargs(interpretation=self.BLOCK))
        self.assertIn("INTERPRETER (model label, unverified)", text)
        self.assertIn("Category: FINANCING", text)
        self.assertIn("Amount: $625,000,000", text)
        self.assertIn("Event Date: N/A", text)
        self.assertLess(text.index("Summary text."), text.index("INTERPRETER"))

        html = FilingNotificationService._build_html_body(**self._kwargs(interpretation=self.BLOCK))
        self.assertIn("FINANCING", html)
        self.assertIn("First &lt;Bank&gt;", html)     # model text is escaped
        self.assertNotIn("First <Bank>", html)

    def test_failed_block_shows_status_only(self):
        text = FilingNotificationService._build_body(
            **self._kwargs(interpretation={"status": "Failed - filing sent to Review Queue (Failures)"})
        )
        self.assertIn("Status: Failed", text)
        self.assertNotIn("Category:", text)

    def test_email_service_passes_block_only_when_present(self):
        notifier = MagicMock()
        service = FilingEmailService(notification_service=notifier)
        summary = MagicMock(summary="S", ticker="A", form="8-K",
                            filing_date="d", accession_number="n")
        service.send(summary_result=summary, filename="f", saved_path="p", source_url="u")
        self.assertNotIn("interpretation", notifier.send_new_filing_notification.call_args.kwargs)
        service.send(summary_result=summary, filename="f", saved_path="p",
                     source_url="u", interpretation=self.BLOCK)
        self.assertEqual(
            notifier.send_new_filing_notification.call_args.kwargs["interpretation"],
            self.BLOCK,
        )
