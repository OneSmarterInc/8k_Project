from django.core.management.base import BaseCommand

from watcher.services.ticker_file_reader import (
    TickerFileError,
    TickerFileReader,
)
from watcher.services.ticker_processor import (
    TickerProcessor,
)
from watcher.models import AutomationRun, ScheduleConfig
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Run the SEC filing watcher using the "
        "configured ticker file"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--auto-index",
            action="store_true",
            help=(
                "After downloading and registering a new "
                "filing, ingest, chunk, and embed it."
            ),
        )
        parser.add_argument(
            "--no-daily-chronicle",
            action="store_true",
            help="Disable sending the daily chronicle email.",
        )

    def handle(self, *args, **options):
        from django.db import connection
        
        # Acquire PostgreSQL advisory lock to guarantee only ONE watcher runs at a time globally
        with connection.cursor() as cursor:
            # 82039147519301 is an arbitrary 64-bit integer for the 8K-Agentic-System Watcher lock
            cursor.execute("SELECT pg_try_advisory_lock(82039147519301)")
            acquired = cursor.fetchone()[0]
            if not acquired:
                self.stdout.write(self.style.ERROR("\n[ABORT] Another SEC watcher instance is currently running. Exiting safely to prevent overlaps.\n"))
                return

        auto_index = bool(
            options.get("auto_index")
        )
        
        daily_chronicle = not bool(
            options.get("no_daily_chronicle")
        )

        try:
            tickers = (
                TickerFileReader()
                .read()
            )

        except TickerFileError as exc:
            self.stderr.write(
                self.style.ERROR(
                    str(exc)
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Watcher started: "
                f"{len(tickers)} ticker(s)"
            )
        )

        self.stdout.write(
            "Automatic indexing: "
            + (
                "ENABLED"
                if auto_index
                else "DISABLED"
            )
        )

        run = AutomationRun.objects.create()
        self.stdout.write(f"Created AutomationRun ID: {run.id}")

        processor = TickerProcessor(
            auto_index=auto_index,
            automation_run=run,
            daily_chronicle=daily_chronicle,
        )

        total_discovered = 0
        total_downloaded = 0
        total_skipped = 0
        total_failed = 0

        total_indexed = 0
        total_index_failed = 0

        total_shards_expected = 0
        total_shards_parsed = 0

        processed = 0
        invalid = 0

        from watcher.models import ScheduleConfig

        for index, ticker in enumerate(
            tickers,
            start=1,
        ):
            # Abort if the user toggles off the background automation
            config = ScheduleConfig.objects.first()
            if config and not config.is_active:
                self.stdout.write(self.style.WARNING("\nAutomation toggled OFF by user (ScheduleConfig inactive). Aborting active run...\n"))
                break

            # We now print ticker processing info only if there were downloads
            try:
                result = processor.process(
                    ticker
                )

            except ValueError as exc:
                invalid += 1

                self.stderr.write(
                    self.style.WARNING(
                        f"{ticker}: skipped - {exc}"
                    )
                )

                continue

            except Exception as exc:
                total_failed += 1

                self.stderr.write(
                    self.style.ERROR(
                        f"{ticker}: "
                        f"processing failed - {exc}"
                    )
                )

                continue

            processed += 1

            total_discovered += (
                result["discovered"]
            )

            total_downloaded += (
                result["downloaded"]
            )

            total_skipped += (
                result["skipped"]
            )

            total_failed += (
                result["failed"]
            )

            total_indexed += (
                result.get(
                    "indexed",
                    0,
                )
            )

            total_index_failed += (
                result.get(
                    "index_failed",
                    0,
                )
            )

            total_shards_expected += result.get("shards_expected", 0)
            total_shards_parsed += result.get("shards_parsed", 0)

            self.stdout.write("")
            self.stdout.write(
                f"[{index}/{len(tickers)}] "
                f"Processing {ticker}..."
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{ticker}: "
                    f"discovered="
                    f"{result['discovered']} "
                    f"downloaded="
                    f"{result['downloaded']} "
                    f"skipped="
                    f"{result['skipped']} "
                    f"failed="
                    f"{result['failed']} "
                    f"indexed="
                    f"{result.get('indexed', 0)} "
                    f"index_failed="
                    f"{result.get('index_failed', 0)}"
                )
            )

            for (
                form,
                form_result,
            ) in result["forms"].items():
                if form_result["downloaded"] > 0 or form_result["skipped"] > 0:
                    self.stdout.write(
                        f"  {form}: "
                        f"discovered="
                        f"{form_result['discovered']} "
                        f"downloaded="
                        f"{form_result['downloaded']} "
                        f"skipped="
                        f"{form_result['skipped']} "
                        f"failed="
                        f"{form_result['failed']} "
                        f"indexed="
                        f"{form_result.get('indexed', 0)} "
                        f"index_failed="
                        f"{form_result.get('index_failed', 0)}"
                    )

                for error in (
                    form_result["errors"]
                ):
                    self.stderr.write(
                        self.style.WARNING(
                            f"    {error}"
                        )
                    )

        self.stdout.write("")
        self.stdout.write(
            "=" * 60
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Watcher complete"
            )
        )

        self.stdout.write(
            f"Tickers in file: "
            f"{len(tickers)}"
        )

        self.stdout.write(
            f"Successfully processed: "
            f"{processed}"
        )

        self.stdout.write(
            f"Invalid/skipped tickers: "
            f"{invalid}"
        )

        self.stdout.write(
            f"Documents downloaded: "
            f"{total_downloaded}"
        )

        self.stdout.write(
            f"Duplicates skipped: "
            f"{total_skipped}"
        )

        self.stdout.write(
            f"Failures: "
            f"{total_failed}"
        )

        self.stdout.write(
            f"Filings indexed: "
            f"{total_indexed}"
        )

        self.stdout.write(
            f"Indexing failures: "
            f"{total_index_failed}"
        )
        
        self.stdout.write(
            f"Shards expected: "
            f"{total_shards_expected}"
        )
        
        self.stdout.write(
            f"Shards parsed: "
            f"{total_shards_parsed}"
        )

        from watcher.knowledge_base.models import Filing
        # Reconciliation sweep for unlinked 8-K/A amendments
        unlinked_amendments = Filing.objects.filter(form="8-K/A", amends__isnull=True)
        for amendment in unlinked_amendments:
            if amendment.report_date:
                original = Filing.objects.filter(
                    company=amendment.company,
                    form="8-K",
                    report_date=amendment.report_date,
                ).order_by("-filing_date", "-accepted_at").first()
                if original:
                    amendment.amends = original
                    amendment.save(update_fields=["amends", "updated_at"])

        unlinked_count = Filing.objects.filter(form="8-K/A", amends__isnull=True).count()
        
        self.stdout.write(
            f"Unlinked 8-K/A remaining: "
            f"{unlinked_count}"
        )

        from watcher.models import FilingSummaryCache
        summary_count = FilingSummaryCache.objects.filter(filing__automation_run=run).count()
        # Since email sending is triggered during post-processing and we don't have a direct email model,
        # we'll use the summary count as a proxy for processed/emailed items or 0 if indexing is off.
        email_count = summary_count if auto_index else 0

        # Determine overall run status based on failures
        if total_failed == 0 and total_index_failed == 0 and total_shards_expected == total_shards_parsed:
            final_status = AutomationRun.Status.COMPLETED
        else:
            if total_indexed > 0 or total_downloaded > 0 or total_shards_parsed > 0:
                final_status = AutomationRun.Status.PARTIAL
            else:
                final_status = AutomationRun.Status.FAILED

        run.status = final_status
        run.completed_at = timezone.now()
        run.files_detected = total_discovered
        run.files_processed = total_indexed
        run.shards_expected = total_shards_expected
        run.shards_parsed = total_shards_parsed
        run.summary_generated_count = summary_count
        run.email_sent_count = email_count
        run.unlinked_amendments_count = unlinked_count
        run.save()

        self.stdout.write(f"AutomationRun {run.id} marked as completed.")