from unittest.mock import MagicMock

from django.test import TestCase
from django.utils import timezone

from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.services.filing_pipeline.post_processing import (
    FilingPostProcessingService,
)


class EmailStatusLogTests(TestCase):
    """The 'NOT SENT (check SMTP...)' line appears only after a real send attempt."""

    NOT_SENT = ("EMAIL", "NOT SENT (check SMTP configuration/logs)")

    def setUp(self):
        self.filing = FilingRegistrationService().register(
            ticker="MAIL",
            cik="5555555555",
            company_name="Mail Co",
            form="8-K",
            accession_number="0005-00-000001",
            sequence=1,
            filing_date="2026-09-01",
            primary_document="d.htm",
            local_path="/tmp/d",
            source_url="http://test/d",
            report_date="2026-09-01",
        )
        self.filing.ingestion_status = "indexed"
        self.filing.save(update_fields=["ingestion_status"])

    def _run(self, *, daily_chronicle=True, send_result=True):
        output = MagicMock()
        email_service = MagicMock()
        email_service.send.return_value = send_result
        metadata_service = MagicMock()
        metadata_service.verify_items.return_value = (None, None)

        service = FilingPostProcessingService(
            auto_index=True,
            daily_chronicle=daily_chronicle,
            indexing_service=MagicMock(),
            summary_service=MagicMock(),
            metadata_service=metadata_service,
            email_service=email_service,
            notification_service=MagicMock(recipient=""),
            output_service=output,
        )
        service.run(
            registered_filing=self.filing,
            form="8-K",
            accession_number=self.filing.accession_number,
            metadata=MagicMock(sec_item_codes=""),
            filename="d.htm",
            saved_path="/tmp/d",
            source_url="http://test/d",
        )
        statuses = [c.args for c in output.status.call_args_list]
        return statuses, email_service

    def test_already_sent_logs_only_skipped(self):
        self.filing.email_sent_at = timezone.now()
        self.filing.save(update_fields=["email_sent_at"])
        statuses, email = self._run()
        self.assertIn(("EMAIL", "SKIPPED (Already sent)"), statuses)
        self.assertNotIn(self.NOT_SENT, statuses)
        email.send.assert_not_called()

    def test_daily_chronicle_off_logs_only_skipped(self):
        statuses, email = self._run(daily_chronicle=False)
        self.assertIn(("EMAIL", "SKIPPED (Daily chronicle disabled)"), statuses)
        self.assertNotIn(self.NOT_SENT, statuses)
        email.send.assert_not_called()

    def test_failed_send_still_logs_not_sent(self):
        statuses, _ = self._run(send_result=False)
        self.assertIn(self.NOT_SENT, statuses)

    def test_successful_send_logs_sent(self):
        statuses, _ = self._run(send_result=True)
        self.assertIn(("EMAIL", "SENT SUCCESSFULLY"), statuses)
        self.assertNotIn(self.NOT_SENT, statuses)