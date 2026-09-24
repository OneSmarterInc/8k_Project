# 8-K/A DISABLED: amendment linking removed.
# from watcher.knowledge_base.ingestion.amendment_linker import (
#     link_amendment,
# )
from datetime import date, datetime
from django.db import transaction
from django.utils import timezone

# 8-K/A DISABLED: duplicate live import left behind by the B-001 edit.
# Nothing calls link_amendment any more, and while this import stayed
# live it kept amendment_linker.py loaded on every registration.
# from watcher.knowledge_base.ingestion.amendment_linker import (
#     link_amendment,
# )
from watcher.knowledge_base.models import (
    Company,
    Filing,
    IngestionJob,
)
from watcher.services.item_codes import (
    format_item_codes,
)
from watcher.services.market_session import (
    VALID_ENTRY_RULES,
)
def _normalize_report_date(value):
    """
    W-037: coerce SEC's report date to a date or None.

    SEC sends "" when there is no report date. "" in a DateField raises
    ValidationError, which made the whole registration fail: the file
    was downloaded but never recorded.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None

class FilingRegistrationService:
    """
    Registers a successfully downloaded SEC filing in PostgreSQL.

    This does not perform parsing/chunking.
    It only creates the durable DB record and queues it for ingestion.

    accepted_at, entry_session, and entry_rule are optional so
    existing callers that do not resolve market-session metadata
    remain compatible.
    """


    def __init__(self, automation_run=None):
        self.automation_run = automation_run

    @transaction.atomic
    def register(
        self,
        *,
        ticker,
        cik,
        company_name,
        form,
        accession_number,
        sequence,
        filing_date,
        primary_document,
        local_path,
        source_url,
        accepted_at=None,
        entry_session=None,
        entry_rule=None,
        sec_item_codes="",
        parsed_item_codes="",
        item_codes_match=None,
        report_date=None,
        flag=False,
        flag_reason="",
    ):

        ticker = str(
            ticker
        ).strip().upper()

        cik = str(
            cik
        ).strip()

        accession_number = str(
            accession_number
        ).strip()

        sequence = int(
            sequence
        )

                # W-037: "" or malformed -> None.
        report_date = _normalize_report_date(
            report_date
        )

                # W-036: always store "1.01;9.01", never str(tuple).
        sec_item_codes = format_item_codes(
            sec_item_codes
        )

        parsed_item_codes = format_item_codes(
            parsed_item_codes
        )

        # --------------------------------
        # Entry-rule provenance validation
        # --------------------------------

        entry_rule = (
            str(entry_rule)
            .strip()
            .upper()
            if entry_rule
            else None
        )

        if (
            entry_session is not None
            and not entry_rule
        ):
            raise ValueError(
                "entry_rule is required when "
                "entry_session is provided."
            )

        if (
            entry_rule is not None
            and entry_rule
            not in VALID_ENTRY_RULES
        ):
            raise ValueError(
                f"Unsupported entry_rule: "
                f"{entry_rule}"
            )

        # --------------------------------
        # Company
        # --------------------------------

        company, _ = (
            Company.objects.update_or_create(
                cik=cik,
                defaults={
                    "ticker": ticker,
                    "name": str(
                        company_name
                        or ""
                    ).strip(),
                },
            )
        )

        # --------------------------------
        # Filing
        # --------------------------------

        filing, created = (
            Filing.objects.get_or_create(
                company=company,
                accession_number=(
                    accession_number
                ),
                sequence=sequence,

                defaults={

                    "form": form,

                    "filing_date": (
                        filing_date
                    ),

                    "accepted_at": (
                        accepted_at
                    ),

                    "entry_session": (
                        entry_session
                    ),

                    "entry_rule": (
                        entry_rule
                    ),

                    "report_date": (
                        report_date
                    ),

                    "sec_item_codes": (
                        sec_item_codes
                    ),

                    "parsed_item_codes": (
                        parsed_item_codes
                    ),

                    "item_codes_match": (
                        item_codes_match
                    ),

                    "flag": flag,

                    "flag_reason": (
                        flag_reason
                    ),

                    "primary_document": (
                        primary_document
                        or ""
                    ),

                    "local_path": str(
                        local_path
                    ),

                    "source_url": (
                        source_url
                        or ""
                    ),

                    "downloaded_at": (
                        timezone.now()
                    ),

                    "ingestion_status": (
                        Filing
                        .IngestionStatus
                        .PENDING
                    ),

                    "automation_run": (
                        self.automation_run
                    ),
                },
            )
        )

        # --------------------------------
        # Existing filing update
        # --------------------------------

        if not created:

            filing.form = form

            filing.filing_date = (
                filing_date
            )

            filing.primary_document = (
                primary_document
                or ""
            )

            filing.local_path = str(
                local_path
            )

            filing.source_url = (
                source_url
                or ""
            )

            update_fields = [
                "form",
                "filing_date",
                "primary_document",
                "local_path",
                "source_url",
            ]

            if self.automation_run is not None:

                filing.automation_run = (
                    self.automation_run
                )

                update_fields.append(
                    "automation_run"
                )

            if accepted_at is not None:

                filing.accepted_at = (
                    accepted_at
                )

                update_fields.append(
                    "accepted_at"
                )

            # --------------------------------
            # Entry-session provenance
            # --------------------------------

            if entry_session is not None:

                # Historical row already has an entry session,
                # but its originating rule is unknown.
                # Do not silently assign provenance.
                if (
                    filing.entry_session
                    is not None
                    and not filing.entry_rule
                ):
                    raise ValueError(
                        "Existing filing has an "
                        "entry_session without an "
                        "entry_rule. A full "
                        "entry-session re-stamp "
                        "is required."
                    )

                # Do not silently mix two entry-rule
                # regimes in the same stored dataset.
                if (
                    filing.entry_rule
                    and entry_rule
                    and filing.entry_rule
                    != entry_rule
                ):
                    raise ValueError(
                        "ENTRY_RULE changed from "
                        f"{filing.entry_rule} "
                        f"to {entry_rule}. "
                        "A full entry-session "
                        "re-stamp is required."
                    )

                filing.entry_session = (
                    entry_session
                )

                filing.entry_rule = (
                    entry_rule
                )

                update_fields.extend(
                    [
                        "entry_session",
                        "entry_rule",
                    ]
                )

            if report_date is not None:

                filing.report_date = (
                    report_date
                )

                update_fields.append(
                    "report_date"
                )

            if sec_item_codes:

                filing.sec_item_codes = (
                    sec_item_codes
                )

                update_fields.append(
                    "sec_item_codes"
                )

            if parsed_item_codes:

                filing.parsed_item_codes = (
                    parsed_item_codes
                )

                update_fields.append(
                    "parsed_item_codes"
                )

            if item_codes_match is not None:

                filing.item_codes_match = (
                    item_codes_match
                )

                update_fields.append(
                    "item_codes_match"
                )

            filing.flag = flag

            filing.flag_reason = (
                flag_reason
            )

            update_fields.extend(
                [
                    "flag",
                    "flag_reason",
                ]
            )

            if filing.downloaded_at is None:

                filing.downloaded_at = (
                    timezone.now()
                )

                update_fields.append(
                    "downloaded_at"
                )

            update_fields.append(
                "updated_at"
            )

            filing.save(
                update_fields=update_fields
            )

        # --------------------------------
        # 8-K/A amendment relationship
        # 8-K/A DISABLED: amendment linking removed.
        # --------------------------------

        # if form == "8-K/A":
        #     link_amendment(
        #         filing
        #     )

        # --------------------------------
        # Queue ingestion
        # --------------------------------

        IngestionJob.objects.get_or_create(
            filing=filing,
            defaults={
                "status": (
                    IngestionJob
                    .Status
                    .PENDING
                ),
            },
        )

        return filing