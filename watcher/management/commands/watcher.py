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

        processed = 0
        invalid = 0

        from django.core.cache import cache

        for index, ticker in enumerate(
            tickers,
            start=1,
        ):
            # Abort if the user toggles off the background automation
            if cache.get("abort_automation_run"):
                self.stdout.write(self.style.WARNING("\nAutomation toggled OFF by user. Aborting active run...\n"))
                cache.delete("abort_automation_run")
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

        from watcher.models import FilingSummaryCache
        summary_count = FilingSummaryCache.objects.filter(filing__automation_run=run).count()
        # Since email sending is triggered during post-processing and we don't have a direct email model,
        # we'll use the summary count as a proxy for processed/emailed items or 0 if indexing is off.
        email_count = summary_count if auto_index else 0

        # Determine overall run status based on failures
        if total_failed == 0 and total_index_failed == 0:
            final_status = AutomationRun.Status.COMPLETED
        else:
            if total_indexed > 0 or total_downloaded > 0:
                final_status = AutomationRun.Status.PARTIAL
            else:
                final_status = AutomationRun.Status.FAILED

        run.status = final_status
        run.completed_at = timezone.now()
        run.files_detected = total_discovered
        run.files_processed = total_indexed
        run.summary_generated_count = summary_count
        run.email_sent_count = email_count
        run.save()

        self.stdout.write(f"AutomationRun {run.id} marked as completed.")