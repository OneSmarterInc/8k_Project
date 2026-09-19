from watcher.knowledge_base.models import FailureEvent
from watcher.services.failure_tracking_service import (
    FailureTrackingService,
)


class FilingPostProcessingService:
    """
    Preserve the established post-registration flow:

        index -> item verification -> summary -> email
    """

    def __init__(
        self,
        *,
        auto_index,
        daily_chronicle=True,
        indexing_service,
        summary_service,
        metadata_service,
        email_service,
        notification_service,
        output_service,
    ):
        self.auto_index = bool(auto_index)
        self.daily_chronicle = bool(daily_chronicle)

        self.indexing_service = indexing_service
        self.summary_service = summary_service
        self.metadata_service = metadata_service
        self.email_service = email_service
        self.notification_service = notification_service
        self.output = output_service

    @staticmethod
    def _result():
        return {
            "indexed": 0,
            "index_failed": 0,
            "errors": [],
        }

    def run(
        self,
        *,
        registered_filing,
        form,
        accession_number,
        metadata,
        filename,
        saved_path,
        source_url,
    ):
        result = self._result()

        if not self.auto_index:
            self.output.status("INDEXING", "DISABLED")
            self.output.status("SUMMARY", "NOT GENERATED")
            self.output.status("EMAIL", "NOT SENT")
            self.output.finish()
            return result

        if self.indexing_service is None:
            self.output.status("INDEXING", "NOT AVAILABLE")
            self.output.status("SUMMARY", "NOT GENERATED")
            self.output.status("EMAIL", "NOT SENT")
            self.output.finish()
            return result

        # -------------------------
        # INDEXING
        # -------------------------
        try:
            self.indexing_service.index_filing(
                registered_filing
            )

            result["indexed"] = 1

            self.output.status(
                "INDEXING",
                "SUCCESS",
            )

        except Exception as exc:

            result["index_failed"] = 1

            result["errors"].append(
                "Knowledge-base indexing failed for "
                f"{accession_number}: {exc}"
            )

            self.output.status(
                "INDEXING",
                f"FAILED ({exc})",
            )

            FailureTrackingService.record(
                filing=registered_filing,
                stage=FailureEvent.Stage.INDEXING,
                code=FailureEvent.Code.INDEX_FAILED,
                message=str(exc),
            )

            return result


        # -------------------------
        # ITEM VERIFICATION
        # -------------------------
        item_verification, verification_error = (
            self.metadata_service.verify_items(
                filing=registered_filing,
                form=form,
                sec_item_codes=metadata.sec_item_codes,
            )
        )

        self.output.item_verification(
            verification=item_verification,
            error=verification_error,
        )

        if item_verification is not None:

            registered_filing.parsed_item_codes = (
                item_verification.parsed_items
            )

            registered_filing.item_codes_match = (
                item_verification.matched
            )

            registered_filing.save(
                update_fields=[
                    "parsed_item_codes",
                    "item_codes_match",
                    "updated_at",
                ]
            )


        # -------------------------
        # SUMMARY
        # -------------------------
        if self.summary_service is None:
            self.output.status(
                "SUMMARY",
                "NOT AVAILABLE",
            )

            self.output.status(
                "EMAIL",
                "NOT SENT",
            )

            self.output.finish()
            return result


        try:

            summary_result = (
                self.summary_service
                .summarize_filing(
                    registered_filing.id
                )
            )

            self.output.status(
                "SUMMARY",
                "GENERATED SUCCESSFULLY",
            )

            self.output.status(
                "POSTGRESQL",
                "SUMMARY STORED/REUSED",
            )

        except Exception as exc:

            result["errors"].append(
                "Filing summary failed for "
                f"{accession_number}: {exc}"
            )

            self.output.status(
                "SUMMARY",
                f"FAILED ({exc})",
            )

            FailureTrackingService.record(
                filing=registered_filing,
                stage=FailureEvent.Stage.SUMMARY,
                code=FailureEvent.Code.SUMMARY_FAILED,
                message=str(exc),
            )

            self.output.status(
                "EMAIL",
                "NOT SENT",
            )

            self.output.finish()

            return result


        # -------------------------
        # EMAIL
        # -------------------------
        try:

            if self.daily_chronicle:

                email_sent = (
                    self.email_service.send(
                        summary_result=summary_result,
                        filename=filename,
                        saved_path=saved_path,
                        source_url=source_url,
                        metadata=metadata,
                        item_verification=item_verification,
                    )
                )

            else:

                email_sent = False

                self.output.status(
                    "EMAIL",
                    "SKIPPED (Daily chronicle disabled)",
                )


        except Exception as exc:

            self.output.status(
                "EMAIL",
                f"FAILED ({exc})",
            )

            FailureTrackingService.record(
                filing=registered_filing,
                stage=FailureEvent.Stage.EMAIL,
                code=FailureEvent.Code.EMAIL_FAILED,
                message=str(exc),
            )

            self.output.finish()

            return result


        if email_sent:

            self.output.status(
                "EMAIL",
                "SENT SUCCESSFULLY",
            )

            recipient = getattr(
                self.notification_service,
                "recipient",
                "",
            )

            if recipient:
                self.output.status(
                    "Recipient",
                    recipient,
                )

        else:

            self.output.status(
                "EMAIL",
                "NOT SENT (check SMTP configuration/logs)",
            )


        self.output.finish()

        return result