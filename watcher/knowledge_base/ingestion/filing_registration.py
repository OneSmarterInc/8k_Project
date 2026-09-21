from django.db import transaction
from django.utils import timezone

from watcher.knowledge_base.models import (
    Company,
    Filing,
    IngestionJob,
)


class FilingRegistrationService:
    """
    Registers a successfully downloaded SEC filing in PostgreSQL.

    This does not perform parsing/chunking.
    It only creates the durable DB record and queues it for ingestion.

    accepted_at, entry_session, and entry_rule are optional so
    existing callers that do not resolve market-session metadata
    remain compatible.
    """

    VALID_ENTRY_RULES = {
        "T_PLUS_1",
        "SAME_SESSION",
    }

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
            not in self.VALID_ENTRY_RULES
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
        # --------------------------------

        if (
            form == "8-K/A"
            and filing.report_date
        ):

            original = (
                Filing.objects.filter(
                    company=company,
                    form="8-K",
                    report_date=(
                        filing.report_date
                    ),
                )
                .order_by(
                    "-filing_date",
                    "-accepted_at",
                )
                .first()
            )

            if (
                original
                and filing.amends != original
            ):

                filing.amends = original

                filing.save(
                    update_fields=[
                        "amends",
                        "updated_at",
                    ]
                )

        # --------------------------------
        # Queue ingestion
        # --------------------------------

        if form == "8-K/A" and filing.report_date:
            original = Filing.objects.filter(
                company=company,
                form="8-K",
                report_date=filing.report_date,
            ).order_by("-filing_date", "-accepted_at").first()
            if original and filing.amends != original:
                filing.amends = original
                filing.save(update_fields=["amends", "updated_at"])


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