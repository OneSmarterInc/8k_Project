import traceback

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from watcher.models import (
    AutomationRun,
    ScheduleConfig,
    FilingSummaryCache,
)
from watcher.knowledge_base.models import (
    FailureEvent,
    Filing,
)
from watcher.knowledge_base.ingestion.amendment_linker import (
    link_amendment,
)
from watcher.services.failure_tracking_service import (
    FailureTrackingService,
)
from watcher.services.ticker_file_reader import (
    TickerFileError,
    TickerFileReader,
)
from watcher.services.ticker_processor import (
    TickerProcessor,
)
from watcher.services.watcher_lock import (
    WATCHER_LOCK_ID,
)


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
        """
        Run one SEC watcher execution.

        W-024 lifecycle guarantees:

        1. PostgreSQL advisory lock prevents overlapping watcher runs.
        2. Normal runs retain the existing AutomationRun finalization.
        3. Unexpected BaseException events, such as KeyboardInterrupt,
           mark a RUNNING AutomationRun as FAILED.
        4. Aborted runs generate a FailureEvent audit record.
        5. The advisory lock is explicitly released in the outer finally.
        6. Hard process death remains recoverable through the stale-run
           reconciliation implemented in watcher_lock.py.

        W-027:
        Amendment matching is delegated to the shared amendment linker.
        """

        run = None
        lock_acquired = False

        auto_index = bool(
            options.get("auto_index")
        )

        daily_chronicle = not bool(
            options.get("no_daily_chronicle")
        )

        # --------------------------------------------------
        # Run counters
        # --------------------------------------------------

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

        unlinked_count = 0
        summary_count = 0
        email_count = 0

        final_status = AutomationRun.Status.FAILED

        try:
            # --------------------------------------------------
            # W-024:
            # Acquire the PostgreSQL advisory lock.
            # --------------------------------------------------

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    [WATCHER_LOCK_ID],
                )

                lock_acquired = bool(
                    cursor.fetchone()[0]
                )

            if not lock_acquired:
                self.stdout.write(
                    self.style.ERROR(
                        "\n[ABORT] Another SEC watcher instance "
                        "is currently running. Exiting safely "
                        "to prevent overlaps.\n"
                    )
                )

                return

            # --------------------------------------------------
            # Read configured tickers
            # --------------------------------------------------

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

            # --------------------------------------------------
            # Create durable AutomationRun
            # --------------------------------------------------

            run = AutomationRun.objects.create()

            self.stdout.write(
                f"Created AutomationRun ID: {run.id}"
            )

            # --------------------------------------------------
            # Watcher processing
            # --------------------------------------------------

            try:
                processor = TickerProcessor(
                    auto_index=auto_index,
                    automation_run=run,
                    daily_chronicle=daily_chronicle,
                )

                for index, ticker in enumerate(
                    tickers,
                    start=1,
                ):
                    # Abort active run when scheduler is toggled off.
                    config = (
                        ScheduleConfig.objects.first()
                    )

                    if (
                        config
                        and not config.is_active
                    ):
                        self.stdout.write(
                            self.style.WARNING(
                                "\nAutomation toggled OFF by user "
                                "(ScheduleConfig inactive). "
                                "Aborting active run...\n"
                            )
                        )

                        break

                    try:
                        result = processor.process(
                            ticker
                        )

                    except ValueError as exc:
                        invalid += 1

                        self.stderr.write(
                            self.style.WARNING(
                                f"{ticker}: "
                                f"skipped - {exc}"
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

                    total_shards_expected += (
                        result.get(
                            "shards_expected",
                            0,
                        )
                    )

                    total_shards_parsed += (
                        result.get(
                            "shards_parsed",
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

                        if (
                            form_result["downloaded"] > 0
                            or form_result["skipped"] > 0
                        ):
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

                # --------------------------------------------------
                # Watcher completion output
                # --------------------------------------------------

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

                # --------------------------------------------------
                # W-027:
                # Shared 8-K/A amendment reconciliation.
                #
                # Amendment matching policy now lives exclusively
                # in amendment_linker.py.
                # --------------------------------------------------

                unlinked_amendments = (
                    Filing.objects.filter(
                        form="8-K/A",
                        amends__isnull=True,
                    )
                    .exclude(
                        flag_reason=(
                            "AMBIGUOUS_AMENDMENT_TARGET"
                        )
                    )
                )

                for amendment in (
                    unlinked_amendments.iterator()
                ):
                    link_amendment(
                        amendment
                    )

                unlinked_count = (
                    Filing.objects.filter(
                        form="8-K/A",
                        amends__isnull=True,
                    )
                    .count()
                )

                self.stdout.write(
                    f"Unlinked 8-K/A remaining: "
                    f"{unlinked_count}"
                )

                # --------------------------------------------------
                # Summary / email counts
                # --------------------------------------------------

                summary_count = (
                    FilingSummaryCache.objects.filter(
                        filing__automation_run=run
                    )
                    .count()
                )

                # Preserve existing behaviour.
                email_count = (
                    summary_count
                    if auto_index
                    else 0
                )

                # --------------------------------------------------
                # Final status calculation
                # --------------------------------------------------

                if (
                    total_failed == 0
                    and total_index_failed == 0
                    and (
                        total_shards_expected
                        == total_shards_parsed
                    )
                ):
                    final_status = (
                        AutomationRun
                        .Status
                        .COMPLETED
                    )

                else:
                    if (
                        total_indexed > 0
                        or total_downloaded > 0
                        or total_shards_parsed > 0
                    ):
                        final_status = (
                            AutomationRun
                            .Status
                            .PARTIAL
                        )

                    else:
                        final_status = (
                            AutomationRun
                            .Status
                            .FAILED
                        )

            except Exception as exc:
                # Ordinary watcher exceptions:
                # log them and finalize the run as FAILED.
                self.stderr.write(
                    self.style.ERROR(
                        "Watcher crashed unexpectedly: "
                        f"{exc}"
                    )
                )

                traceback.print_exc()

                final_status = (
                    AutomationRun.Status.FAILED
                )

            finally:
                # --------------------------------------------------
                # Normal AutomationRun finalization
                # --------------------------------------------------

                try:
                    unlinked_count = (
                        Filing.objects.filter(
                            form="8-K/A",
                            amends__isnull=True,
                        )
                        .count()
                    )

                    summary_count = (
                        FilingSummaryCache.objects.filter(
                            filing__automation_run=run
                        )
                        .count()
                    )

                    email_count = (
                        summary_count
                        if auto_index
                        else 0
                    )

                except Exception as exc:
                    # Preserve initialized fallback values if final
                    # statistics cannot be calculated.
                    self.stderr.write(
                        self.style.ERROR(
                            "Error computing final stats: "
                            f"{exc}"
                        )
                    )

                run.status = final_status
                run.completed_at = timezone.now()

                run.files_detected = (
                    total_discovered
                )

                run.files_processed = (
                    total_indexed
                )

                run.shards_expected = (
                    total_shards_expected
                )

                run.shards_parsed = (
                    total_shards_parsed
                )

                run.summary_generated_count = (
                    summary_count
                )

                run.email_sent_count = (
                    email_count
                )

                run.unlinked_amendments_count = (
                    unlinked_count
                )

                run.save()

                self.stdout.write(
                    f"AutomationRun {run.id} "
                    f"marked as {final_status}."
                )

        except BaseException as exc:
            # --------------------------------------------------
            # W-024 Part A
            #
            # Exception does not include KeyboardInterrupt or
            # SystemExit. BaseException does.
            #
            # This outer handler guarantees that an abnormal
            # interpreter-level interruption does not intentionally
            # leave an AutomationRun marked RUNNING.
            # --------------------------------------------------

            if run is not None:
                aborted_at = timezone.now()

                try:
                    (
                        AutomationRun.objects
                        .filter(
                            pk=run.pk,
                            status=(
                                AutomationRun
                                .Status
                                .RUNNING
                            ),
                        )
                        .update(
                            status=(
                                AutomationRun
                                .Status
                                .FAILED
                            ),
                            completed_at=aborted_at,
                        )
                    )

                    # Keep in-memory object consistent as well.
                    run.status = (
                        AutomationRun.Status.FAILED
                    )

                    run.completed_at = (
                        aborted_at
                    )

                except Exception as finalize_exc:
                    self.stderr.write(
                        self.style.ERROR(
                            "Unable to finalize aborted "
                            "AutomationRun "
                            f"{run.pk}: "
                            f"{finalize_exc}"
                        )
                    )

                # --------------------------------------------------
                # W-024 failure audit
                # --------------------------------------------------

                try:
                    FailureTrackingService.record(
                        stage=(
                            FailureEvent
                            .Stage
                            .DISCOVERY
                        ),
                        code=(
                            FailureEvent
                            .Code
                            .UNKNOWN
                        ),
                        message=(
                            f"AutomationRun {run.pk} "
                            "aborted unexpectedly: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                    )

                except Exception as tracking_exc:
                    self.stderr.write(
                        self.style.ERROR(
                            "Unable to record aborted "
                            "AutomationRun failure: "
                            f"{tracking_exc}"
                        )
                    )

            # Preserve KeyboardInterrupt/SystemExit/etc.
            raise

        finally:
            # --------------------------------------------------
            # W-024:
            # Explicitly release the session-level PostgreSQL
            # advisory lock whenever possible.
            # --------------------------------------------------

            if lock_acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(%s)",
                            [WATCHER_LOCK_ID],
                        )

                        released = bool(
                            cursor.fetchone()[0]
                        )

                    if not released:
                        self.stderr.write(
                            self.style.WARNING(
                                "Watcher advisory lock was "
                                "already released or was no "
                                "longer owned by this database "
                                "session."
                            )
                        )

                except Exception as unlock_exc:
                    # Never replace the original watcher exception
                    # with a lock-cleanup exception.
                    self.stderr.write(
                        self.style.ERROR(
                            "Unable to explicitly release "
                            "watcher advisory lock: "
                            f"{unlock_exc}"
                        )
                    )