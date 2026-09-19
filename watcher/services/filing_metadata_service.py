from dataclasses import dataclass
from datetime import date, datetime

from watcher.services.company_verification_service import (
    CompanyVerificationService,
)
from watcher.services.item_verification_service import (
    ItemVerificationService,
)
from watcher.services.market_session import (
    MarketSessionService,
)
from watcher.services.timestamp_service import (
    TimestampService,
)
from watcher.knowledge_base.models import FailureEvent
from watcher.services.failure_tracking_service import (
    FailureTrackingService,
)


@dataclass
class FilingMetadata:

    acceptance_datetime: str = ""

    accepted_at: datetime | None = None
    accepted_at_display: str = ""

    entry_session: date | None = None

    sec_item_codes: tuple[str, ...] = ()

    parsed_item_codes: tuple[str, ...] = ()

    item_codes_match: bool | None = None

    company_verification: object = None

    timestamp_error: str = ""
    session_error: str = ""
    company_verification_error: str = ""
    item_verification_error: str = ""

    flag: bool = False
    flag_reason: str = ""


class FilingMetadataService:

    def __init__(
        self,
        *,
        timestamp_service=None,
        market_session_service=None,
        item_verification_service=None,
        company_verification_service=None,
    ):

        self.timestamp_service = (
            timestamp_service
            or TimestampService()
        )

        self.market_session_service = (
            market_session_service
            or MarketSessionService()
        )

        self.item_verification_service = (
            item_verification_service
            or ItemVerificationService()
        )

        self.company_verification_service = (
            company_verification_service
            or CompanyVerificationService()
        )


    @staticmethod
    def _normalize_item_codes(value):

        if not value:
            return ()

        if isinstance(value, str):
            values = value.split(",")

        else:
            values = value

        result = []
        seen = set()

        for item in values:

            cleaned = str(item).strip()

            if not cleaned:
                continue

            if cleaned in seen:
                continue

            seen.add(cleaned)
            result.append(cleaned)

        return tuple(result)


    def prepare(
        self,
        *,
        filing,
        expected_cik="",
        expected_company_name="",
        expected_ticker="",
    ):

        metadata = FilingMetadata(
            acceptance_datetime=str(
                filing.get(
                    "acceptance_datetime",
                    "",
                )
                or ""
            ).strip(),

            sec_item_codes=(
                self._normalize_item_codes(
                    filing.get(
                        "item_codes",
                        (),
                    )
                )
            ),
        )


        # -------------------------------
        # SEC item verification
        # -------------------------------

        if metadata.sec_item_codes:

            try:

                verification_result, error = (
                    self.verify_items(
                        filing=filing,
                        form=filing.get(
                            "form",
                            "",
                        ),
                        sec_item_codes=(
                            metadata.sec_item_codes
                        ),
                    )
                )

                if verification_result:

                    metadata.parsed_item_codes = (
                        verification_result.parsed_items
                    )

                    metadata.item_codes_match = (
                        verification_result.matched
                    )

                if error:
                    metadata.item_verification_error = error


            except Exception as exc:

                metadata.item_verification_error = str(exc)
                metadata.item_codes_match = None


        # -------------------------------
        # Acceptance timestamp
        # -------------------------------

        if not metadata.acceptance_datetime:

            metadata.flag = True
            metadata.flag_reason = (
                "MISSING_ACCEPTANCE_TS"
            )


        else:

            try:

                metadata.accepted_at = (
                    self.timestamp_service
                    .parse_acceptance(
                        metadata.acceptance_datetime
                    )
                )

                metadata.accepted_at_display = (
                    self.timestamp_service
                    .format_eastern(
                        metadata.accepted_at
                    )
                )


            except Exception as exc:

                metadata.timestamp_error = str(exc)
                metadata.accepted_at = None
                metadata.accepted_at_display = ""

                metadata.flag = True
                metadata.flag_reason = (
                    "MALFORMED_ACCEPTANCE_TS"
                )

                FailureTrackingService.record(
                    stage=FailureEvent.Stage.METADATA,
                    code=FailureEvent.Code.METADATA_FAILED,
                    message=str(exc),
                )


        # -------------------------------
        # Entry trading session
        # -------------------------------

        if metadata.accepted_at is not None:

            try:

                metadata.entry_session = (
                    self.market_session_service
                    .entry_session(
                        metadata.accepted_at
                    )
                )


            except Exception as exc:

                metadata.session_error = str(exc)
                metadata.entry_session = None

                metadata.flag = True
                metadata.flag_reason = (
                    "NO_SESSION_RESOLVED"
                )

                FailureTrackingService.record(
                    stage=FailureEvent.Stage.METADATA,
                    code=FailureEvent.Code.METADATA_FAILED,
                    message=str(exc),
                )


        # -------------------------------
        # Company verification
        # -------------------------------

        sec_cik = str(
            filing.get(
                "sec_cik",
                "",
            )
            or ""
        ).strip()


        if sec_cik:

            try:

                metadata.company_verification = (
                    self.company_verification_service
                    .verify(
                        expected_cik=expected_cik,
                        sec_cik=sec_cik,
                        expected_company_name=(
                            expected_company_name
                        ),
                        sec_company_name=(
                            filing.get(
                                "sec_company_name",
                                "",
                            )
                        ),
                        expected_ticker=expected_ticker,
                        sec_tickers=(
                            filing.get(
                                "sec_tickers",
                                (),
                            )
                        ),
                    )
                )


            except Exception as exc:

                metadata.company_verification_error = str(exc)


        return metadata



    def verify_items(
        self,
        *,
        filing,
        form,
        sec_item_codes,
    ):

        if (
            str(form)
            .strip()
            .upper()
            != "8-K"
        ):

            return None, ""


        if not sec_item_codes:

            return None, ""


        try:

            result = (
                self.item_verification_service
                .verify(
                    filing,
                    sec_item_codes,
                )
            )

            return result, ""


        except Exception as exc:

            return None, str(exc)