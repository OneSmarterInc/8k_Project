from datetime import date

from watcher.services.sec_client import SECClient


class FilingDiscovery:
    """
    Retrieves company filings from the SEC submissions API and filters
    them by filing form and filing date.

    In addition to the existing filing metadata, this service also
    carries forward:

        - EDGAR acceptance datetime
        - SEC structured item codes

    These values are metadata only at this stage.

    Timestamp parsing, market-session calculation, PostgreSQL storage,
    indexing, summarization, and email notification remain downstream
    responsibilities.
    """

    SUBMISSIONS_URL = (
        "https://data.sec.gov/submissions/CIK{cik}.json"
    )

    SUPPORTED_FORMS = frozenset({
        "8-K",
        "8-K/A",
        "10-K",
        "10-Q",
    })

    def __init__(
        self,
        client=None,
    ):
        self.client = (
            client
            or SECClient()
        )

    def list_filings(
        self,
        cik,
        form,
        start_date=None,
        end_date=None,
    ):
        form = (
            str(form)
            .strip()
            .upper()
        )

        if form not in self.SUPPORTED_FORMS:
            raise ValueError(
                f"Unsupported filing form: {form}. "
                f"Supported forms: "
                f"{', '.join(sorted(self.SUPPORTED_FORMS))}"
            )

        today = date.today()

        if start_date is None:
            start_date = date(
                today.year,
                1,
                1,
            )

        if end_date is None:
            end_date = today

        if start_date > end_date:
            raise ValueError(
                "start_date cannot be after end_date"
            )

        normalized_cik = (
            str(cik)
            .strip()
            .zfill(10)
        )

        url = self.SUBMISSIONS_URL.format(
            cik=normalized_cik
        )

        data = self.client.get_json(
            url
        )
        sec_cik = str(
            data.get(
                "cik",
                "",
            )
            or ""
        ).strip()

        sec_company_name = str(
            data.get(
                "name",
                "",
            )
            or ""
        ).strip()

        raw_sec_tickers = (
            data.get(
                "tickers",
                [],
            )
            or []
        )

        if isinstance(
            raw_sec_tickers,
            str,
        ):
            sec_tickers = (
                raw_sec_tickers.strip(),
            )

        else:
            sec_tickers = tuple(
                str(ticker).strip().upper()
                for ticker in raw_sec_tickers
                if str(ticker).strip()
            )
        recent = (
            data.get(
                "filings",
                {},
            )
            .get(
                "recent",
                {},
            )
        )

        filings = self._parse_filings_block(
            block=recent,
            form=form,
            start_date=start_date,
            end_date=end_date,
            normalized_cik=normalized_cik,
            sec_cik=sec_cik,
            sec_company_name=sec_company_name,
            sec_tickers=sec_tickers,
        )

        expected_shards = 0
        parsed_shards = 0
        shard_errors = []

        files = data.get("filings", {}).get("files", [])
        for file_info in files:
            try:
                filing_from = date.fromisoformat(file_info.get("filingFrom", ""))
                filing_to = date.fromisoformat(file_info.get("filingTo", ""))
            except (TypeError, ValueError):
                continue
                
            # Check if this shard overlaps with the requested date range
            if filing_from <= end_date and filing_to >= start_date:
                shard_name = file_info.get("name")
                if shard_name:
                    expected_shards += 1
                    shard_url = f"https://data.sec.gov/submissions/{shard_name}"
                    try:
                        shard_data = self.client.get_json(shard_url)
                        shard_filings = self._parse_filings_block(
                            block=shard_data,
                            form=form,
                            start_date=start_date,
                            end_date=end_date,
                            normalized_cik=normalized_cik,
                            sec_cik=sec_cik,
                            sec_company_name=sec_company_name,
                            sec_tickers=sec_tickers,
                        )
                        filings.extend(shard_filings)
                        parsed_shards += 1
                    except Exception as e:
                        shard_errors.append(str(e))

        # ---------------------------------------------------------
        # Preserve existing sorting behavior.
        # ---------------------------------------------------------

        filings.sort(
            key=lambda filing: (
                filing[
                    "filing_date"
                ],
                filing[
                    "accession_number"
                ],
            ),
            reverse=True,
        )

        return {
            "filings": filings,
            "shards_expected": expected_shards,
            "shards_parsed": parsed_shards,
            "shard_errors": shard_errors,
        }

    def _parse_filings_block(
        self,
        block,
        form,
        start_date,
        end_date,
        normalized_cik,
        sec_cik,
        sec_company_name,
        sec_tickers,
    ):
        # ---------------------------------------------------------
        # Existing required SEC metadata.
        # ---------------------------------------------------------

        forms = block.get(
            "form",
            [],
        )

        filing_dates = block.get(
            "filingDate",
            [],
        )

        report_dates = block.get(
            "reportDate",
            [],
        )

        accession_numbers = block.get(
            "accessionNumber",
            [],
        )

        primary_documents = block.get(
            "primaryDocument",
            [],
        )

        primary_document_descriptions = (
            block.get(
                "primaryDocDescription",
                [],
            )
        )

        # ---------------------------------------------------------
        # New additive metadata.
        #
        # IMPORTANT:
        # These arrays are intentionally NOT included in row_count.
        #
        # They are optional metadata for our purposes. A missing
        # optional value must never cause an otherwise valid filing
        # to disappear from discovery.
        # ---------------------------------------------------------

        acceptance_datetimes = block.get(
            "acceptanceDateTime",
            [],
        )

        filing_items = block.get(
            "items",
            [],
        )

        filings = []

        # ---------------------------------------------------------
        # Preserve the existing required-row behavior.
        # ---------------------------------------------------------

        row_count = min(
            len(forms),
            len(filing_dates),
            len(accession_numbers),
            len(primary_documents),
        )

        for index in range(
            row_count
        ):
            filing_form = (
                str(
                    forms[index]
                )
                .strip()
                .upper()
            )

            if filing_form != form:
                continue

            try:
                filing_date = (
                    date.fromisoformat(
                        filing_dates[index]
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            if not (
                start_date
                <= filing_date
                <= end_date
            ):
                continue

            description = (
                primary_document_descriptions[index]
                if index
                < len(
                    primary_document_descriptions
                )
                else ""
            )

            report_date_str = (
                report_dates[index]
                if index < len(report_dates)
                else ""
            )

            report_date = None
            if report_date_str:
                try:
                    report_date = date.fromisoformat(report_date_str)
                except (TypeError, ValueError):
                    pass

            # -----------------------------------------------------
            # EDGAR acceptance datetime.
            #
            # Do NOT parse it here.
            # We preserve the SEC value and let TimestampService
            # perform one canonical parse later.
            # -----------------------------------------------------

            acceptance_datetime = (
                acceptance_datetimes[index]
                if index
                < len(
                    acceptance_datetimes
                )
                else ""
            )

            acceptance_datetime = str(
                acceptance_datetime
                or ""
            ).strip()

            # -----------------------------------------------------
            # SEC item codes.
            #
            # Examples for an 8-K may look like:
            #
            #     1.01
            #     2.02
            #     5.02
            #     8.01
            #     9.01
            #
            # SEC normally supplies the field as comma-separated
            # metadata. Keep it as a normalized list.
            #
            # Do not infer any item that SEC did not provide.
            # -----------------------------------------------------

            raw_items = (
                filing_items[index]
                if index
                < len(
                    filing_items
                )
                else ""
            )

            item_codes = tuple(
                item.strip()
                for item in str(
                    raw_items
                    or ""
                ).split(",")
                if item.strip()
            )

            filings.append(
                {
                    "cik": normalized_cik,

                    "form": filing_form,

                    "filing_date": (
                        filing_date.isoformat()
                    ),

                    "report_date": (
                        report_date.isoformat() if report_date else ""
                    ),

                    "acceptance_datetime": (
                        acceptance_datetime
                    ),

                    "item_codes": (
                        item_codes
                    ),

                    "accession_number": (
                        accession_numbers[index]
                    ),

                    "primary_document": (
                        primary_documents[index]
                    ),

                    "description": (
                        description
                        or ""
                    ),
                    "sec_cik": sec_cik,
                    "sec_company_name": sec_company_name,
                    "sec_tickers": sec_tickers,
                }
            )

        return filings