from pathlib import Path

from watcher.knowledge_base.models import FailureEvent
from watcher.services.failure_tracking_service import (
    FailureTrackingService,
)


class FilingProcessingService:
    """
    Process exactly one discovered filing.

    Stage order:

        resolve document
        -> duplicate check
        -> download
        -> registry mark
        -> metadata
        -> registration
        -> post-processing
    """

    def __init__(
        self,
        *,
        downloader,
        registry,
        registration_service,
        metadata_service,
        post_processing_service,
        output_service,
    ):
        self.downloader = downloader
        self.registry = registry
        self.registration_service = registration_service
        self.metadata_service = metadata_service
        self.post_processing = post_processing_service
        self.output = output_service

    @staticmethod
    def _result():
        return {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "indexed": 0,
            "index_failed": 0,
            "errors": [],
        }

    @staticmethod
    def _merge_post_result(
        result,
        post_result,
    ):

        result["indexed"] += post_result.get(
            "indexed",
            0,
        )

        result["index_failed"] += post_result.get(
            "index_failed",
            0,
        )

        result["errors"].extend(
            post_result.get(
                "errors",
                [],
            )
        )

    def process(
        self,
        *,
        company,
        ticker,
        cik,
        form,
        filing,
    ):

        result = self._result()

        accession_number = filing[
            "accession_number"
        ]

        primary_document = filing[
            "primary_document"
        ]

        filing_date = filing[
            "filing_date"
        ]

        try:

            # --------------------------------
            # Resolve SEC document
            # --------------------------------

            base_url, document = (
                self.downloader.resolve_document(
                    cik=cik,
                    accession_number=(
                        accession_number
                    ),
                    file_type=form,
                    hint_filename=(
                        primary_document
                    ),
                )
            )

            sequence = str(
                document.get(
                    "sequence"
                )
                or ""
            ).strip()

            if not sequence:
                raise ValueError(
                    "SEC document sequence "
                    "could not be resolved"
                )

            # --------------------------------
            # Duplicate check
            # --------------------------------

            if self.registry.is_downloaded(
                cik,
                accession_number,
                sequence,
            ):
                from watcher.knowledge_base.models import Filing
                from watcher.services.failure_tracking_service import FailureTrackingService
                
                existing_filing = Filing.objects.filter(
                    company__cik=cik,
                    accession_number=accession_number,
                    sequence=sequence
                ).first()
                
                if existing_filing:
                    unresolved = FailureTrackingService.unresolved_for_filing(existing_filing)
                    
                    if unresolved.exists():
                        # Unresolved failures exist, resume processing!
                        # We need metadata to run post_processing
                        metadata = self.metadata_service.prepare(
                            filing=filing,
                            expected_cik=cik,
                            expected_company_name=company.get("name", ""),
                            expected_ticker=ticker,
                        )
                        
                        file_type = document.get("type") or form
                        saved_filename = existing_filing.primary_document or Path(existing_filing.local_path).name
                        
                        post_result = self.post_processing.run(
                            registered_filing=existing_filing,
                            form=form,
                            accession_number=accession_number,
                            metadata=metadata,
                            filename=saved_filename,
                            saved_path=existing_filing.local_path,
                            source_url=existing_filing.source_url,
                        )
                        
                        self._merge_post_result(result, post_result)
                        return result
                    else:
                        # Fully processed
                        result["skipped"] = 1
                        return result
                else:
                    # Registry says downloaded but no Filing exists?
                    # This means Registration failed previously. Do not skip!
                    # Continue normal flow (it won't redownload physical file if it exists, but will re-register)
                    pass

            # --------------------------------
            # Resolve filename / type
            # --------------------------------

            file_type = (
                document.get("type")
                or form
            )

            original_filename = (
                document["filename"]
            )

            local_filename = (
                self.downloader
                .build_filename(
                    form=form,
                    file_type=file_type,
                    filing_date=(
                        filing_date
                    ),
                    original_filename=(
                        original_filename
                    ),
                )
            )

            # --------------------------------
            # Download
            # --------------------------------

            download = (
                self.downloader.download(
                    cik=cik,
                    accession_number=(
                        accession_number
                    ),
                    file_type=file_type,
                    sequence=sequence,
                    local_filename=(
                        local_filename
                    ),
                    hint_filename=(
                        primary_document
                    ),
                    document=document,
                    base_url=base_url,
                    ticker=ticker,
                    form=form,
                )
            )

            downloaded_sequence = str(
                download.get(
                    "sequence"
                )
                or sequence
            ).strip()

            # --------------------------------
            # Registry mark
            # --------------------------------

            self.registry.mark_downloaded(
                cik,
                accession_number,
                downloaded_sequence,
            )

            result["downloaded"] = 1

            # --------------------------------
            # Resolve downloaded file metadata
            # --------------------------------

            saved_path = str(
                download["path"]
            )

            saved_filename = (
                Path(
                    saved_path
                ).name
            )

            resolved_form_type = str(
                document.get("type")
                or form
            ).strip()

            source_url = str(
                download.get("url")
                or ""
            ).strip()

            # --------------------------------
            # Filing metadata
            # --------------------------------

            metadata = (
                self.metadata_service
                .prepare(
                    filing=filing,
                    expected_cik=cik,
                    expected_company_name=(
                        company.get(
                            "name",
                            "",
                        )
                    ),
                    expected_ticker=ticker,
                )
            )

            self.output.new_filing(
                ticker=ticker,
                form_type=(
                    resolved_form_type
                ),
                filing_date=filing_date,
                accession_number=(
                    accession_number
                ),
                sequence=(
                    downloaded_sequence
                ),
                filename=saved_filename,
                saved_path=saved_path,
                source_url=source_url,
            )

            # --------------------------------
            # Database registration
            # --------------------------------

            try:

                registered_filing = (
                    self.registration_service
                    .register(
                        ticker=ticker,
                        cik=cik,
                        company_name=(
                            company.get(
                                "name",
                                "",
                            )
                        ),
                        form=form,
                        accession_number=(
                            accession_number
                        ),
                        sequence=(
                            downloaded_sequence
                        ),
                        filing_date=(
                            filing_date
                        ),
                        primary_document=(
                            primary_document
                        ),
                        local_path=(
                            download["path"]
                        ),
                        source_url=(
                            download["url"]
                        ),

                        accepted_at=getattr(
                            metadata,
                            "accepted_at",
                            None,
                        ),

                        entry_session=getattr(
                            metadata,
                            "entry_session",
                            None,
                        ),

                        entry_rule=getattr(
                            metadata,
                            "entry_rule",
                            None,
                        ),

                        report_date=filing.get(
                            "report_date"
                        ),

                        sec_item_codes=getattr(
                            metadata,
                            "sec_item_codes",
                            (),
                        ),

                        parsed_item_codes=getattr(
                            metadata,
                            "parsed_item_codes",
                            (),
                        ),

                        item_codes_match=getattr(
                            metadata,
                            "item_codes_match",
                            None,
                        ),

                        flag=getattr(
                            metadata,
                            "flag",
                            False,
                        ),

                        flag_reason=getattr(
                            metadata,
                            "flag_reason",
                            "",
                        ),
                    )
                )

                self.output.status(
                    "REGISTRATION",
                    "SUCCESS",
                )

                self.output.metadata(
                    metadata=metadata,
                    form=form,
                )

            except Exception as exc:

                result["errors"].append(
                    "Knowledge-base "
                    "registration failed for "
                    f"{accession_number}: "
                    f"{exc}"
                )

                self.output.status(
                    "REGISTRATION",
                    f"FAILED ({exc})",
                )

                FailureTrackingService.record(
                    stage=(
                        FailureEvent
                        .Stage
                        .REGISTRATION
                    ),
                    code=(
                        FailureEvent
                        .Code
                        .REGISTRATION_FAILED
                    ),
                    message=str(exc),
                )

                return result

            # --------------------------------
            # Post-processing
            # --------------------------------

            post_result = (
                self.post_processing.run(
                    registered_filing=(
                        registered_filing
                    ),
                    form=form,
                    accession_number=(
                        accession_number
                    ),
                    metadata=metadata,
                    filename=(
                        saved_filename
                    ),
                    saved_path=(
                        saved_path
                    ),
                    source_url=(
                        source_url
                    ),
                )
            )

            self._merge_post_result(
                result,
                post_result,
            )

            return result

        except Exception as exc:

            result["failed"] = 1

            result["errors"].append(
                f"{accession_number}: "
                f"{exc}"
            )

            return result