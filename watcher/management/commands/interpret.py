"""
Guide 5.7: run the Interpreter as its own command, OUT of the Watcher's
run path. A slow or crashed model can never turn a capture run PARTIAL.

    python manage.py interpret                        # up to 100 unclassified filings
    python manage.py interpret --limit 20 --dry-run   # list, no model calls
    python manage.py interpret --filing-id 412
    python manage.py interpret --ticker AIG
    python manage.py interpret --split train          # ground-truth train set
    python manage.py interpret --reclassify --taxonomy-version 1.1.0

"Unclassified" means no row yet for the current taxonomy AND prompt
version. Re-running is safe: finished filings are skipped. --reclassify
writes NEW rows; old rows are never changed (append-only).

Schedule it separately from the Watcher (cron / systemd timer / Task
Scheduler), e.g. every 20 minutes. Two runs cannot overlap: the second
exits at once because of the advisory lock.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from watcher.interpreter import lock
from watcher.interpreter.prompts import PROMPT_VERSION
from watcher.interpreter.service import (
    InterpreterService,
    ModelUnavailable,
    interpretable_filings,
    pending_filings,
)
from watcher.interpreter.taxonomy import TAXONOMY_VERSION
from watcher.models import InterpreterRun


class Command(BaseCommand):
    help = "Classify 8-K filings with the Interpreter (guide Part 5)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--filing-id", type=int, action="append", dest="filing_ids")
        parser.add_argument("--ticker")
        parser.add_argument(
            "--split",
            choices=["train", "test"],
            help="Only filings in this ground-truth split. The sealed "
                 "slice is reserved for the Auditor and cannot be used.",
        )
        parser.add_argument(
            "--reclassify",
            action="store_true",
            help="Classify again even if a row exists for the current "
                 "versions. Always writes new rows.",
        )
        parser.add_argument(
            "--taxonomy-version",
            help="Safety check: must equal the version in taxonomy.py.",
        )
        parser.add_argument(
            "--max-errors",
            type=int,
            default=3,
            help="Stop after this many model errors in a row (Ollama down).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the filings that would be classified. No model calls.",
        )

    def handle(self, *args, **opts):
        if opts["limit"] <= 0:
            raise CommandError("--limit must be positive.")

        wanted = opts.get("taxonomy_version")
        if wanted and wanted != TAXONOMY_VERSION:
            raise CommandError(
                f"--taxonomy-version {wanted} does not match taxonomy.py "
                f"({TAXONOMY_VERSION}). Bump TAXONOMY_VERSION first."
            )

        if not lock.try_acquire():
            raise CommandError("Another Interpreter run is in progress.")

        try:
            lock.reconcile_stale_runs()
            filings = list(self._select(opts))

            self.stdout.write(
                f"Taxonomy {TAXONOMY_VERSION} · Prompt {PROMPT_VERSION} · "
                f"{len(filings)} filing(s) selected"
            )

            if opts["dry_run"]:
                for filing in filings:
                    self.stdout.write(
                        f"  {filing.id:>7}  {filing.company.ticker:<6} "
                        f"{filing.form:<6} {filing.accession_number}"
                    )
                self.stdout.write(self.style.NOTICE("Dry run: no model calls, nothing saved."))
                return

            if not filings:
                self.stdout.write("Nothing to classify.")
                return

            self._run(filings, opts)
        finally:
            lock.release()

    def _select(self, opts):
        if opts["reclassify"]:
            queryset = interpretable_filings().order_by("-created_at", "-id")
        else:
            queryset = pending_filings()

        if opts.get("filing_ids"):
            queryset = queryset.filter(id__in=opts["filing_ids"])

        if opts.get("ticker"):
            queryset = queryset.filter(company__ticker__iexact=opts["ticker"])

        if opts.get("split"):
            queryset = queryset.filter(ground_truth_split__split=opts["split"])

        return queryset[: opts["limit"]]

    def _run(self, filings, opts):
        service = InterpreterService()

        run = InterpreterRun.objects.create(
            taxonomy_version=TAXONOMY_VERSION,
            prompt_version=PROMPT_VERSION,
            model_name=service.model_label,
            filings_selected=len(filings),
        )

        consecutive_errors = 0
        aborted = False

        try:
            for filing in filings:
                try:
                    row = service.classify(filing, run=run)
                except ModelUnavailable as exc:
                    run.error_count += 1
                    run.error_message = str(exc)[:2000]
                    consecutive_errors += 1
                    self.stderr.write(f"  {filing.id}: model error: {exc}")
                    if consecutive_errors >= opts["max_errors"]:
                        aborted = True
                        self.stderr.write(self.style.ERROR(
                            f"Stopping after {consecutive_errors} model errors in a row."
                        ))
                        break
                    continue

                consecutive_errors = 0

                if row is None:
                    run.skipped_count += 1
                    continue

                run.classified_count += 1
                if row.needs_human_review:
                    run.review_count += 1

                label = row.category or (
                    "ROUTINE" if row.is_material is False else "?"
                )
                conf = "-" if row.confidence is None else f"{row.confidence:.2f}"
                flag = "  -> review" if row.needs_human_review else ""
                reason = f" [{row.failure_code}]" if row.failure_code else ""
                self.stdout.write(
                    f"  {filing.id:>7}  {filing.company.ticker:<6} "
                    f"{label:<17} {conf}{flag}{reason}"
                )

            if aborted and run.classified_count == 0:
                run.status = InterpreterRun.Status.FAILED
            elif run.error_count:
                run.status = InterpreterRun.Status.PARTIAL
            else:
                run.status = InterpreterRun.Status.COMPLETED

        except BaseException as exc:
            run.status = InterpreterRun.Status.FAILED
            run.error_message = (run.error_message or f"{type(exc).__name__}: {exc}")[:2000]
            raise
        finally:
            run.completed_at = timezone.now()
            run.save()

        self.stdout.write(
            f"Run {run.id} {run.status}: classified {run.classified_count}, "
            f"to review {run.review_count}, skipped {run.skipped_count}, "
            f"errors {run.error_count}"
        )
