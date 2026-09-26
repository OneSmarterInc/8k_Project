"""
`manage.py audit` - the Auditor agent.

Separate command, separate advisory lock, separate run table. A slow
or crashed Auditor can never turn a Watcher capture run or an
Interpreter run into a PARTIAL, which is the same isolation the
Interpreter has from the Watcher.

    python manage.py audit --sample 100 --dry-run
    python manage.py audit --sample 100
    python manage.py audit --report --days 30
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from watcher.auditor import lock, sampling, scoring
from watcher.auditor.prompts import AUDIT_PROMPT_VERSION
from watcher.auditor.service import AuditorService
from watcher.interpreter.taxonomy import TAXONOMY_VERSION
from watcher.knowledge_base.generation.ollama_generation_service import (
    GenerationServiceError,
)
from watcher.models import AuditorRun, AuditSample, AuditWindow


class Command(BaseCommand):
    help = "Audit the Interpreter's classifications and the generated summaries."

    def add_arguments(self, parser):
        parser.add_argument("--sample", type=int, default=100)
        parser.add_argument("--sealed", type=int, default=50)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--skip-summaries",
            action="store_true",
            help="Classification check only.",
        )
        parser.add_argument(
            "--report",
            action="store_true",
            help="Print recent windows instead of running an audit.",
        )
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **opts):
        if opts["report"]:
            return self._report(opts["days"])

        if opts["sample"] <= 0:
            raise CommandError("--sample must be positive.")

        if not lock.try_acquire():
            raise CommandError("Another Auditor run is in progress.")

        try:
            lock.reconcile_stale_runs()

            fresh, sealed = sampling.build_audit_sample(
                opts["sample"], opts["sealed"]
            )

            self.stdout.write(
                f"Taxonomy {TAXONOMY_VERSION} · Audit prompt "
                f"{AUDIT_PROMPT_VERSION} · {len(fresh)} fresh, "
                f"{len(sealed)} sealed"
            )

            if opts["dry_run"]:
                for row in fresh + sealed:
                    self.stdout.write(
                        f"  {row.id:>7}  {row.filing.company.ticker:<6} "
                        f"{row.category or 'ROUTINE':<18} "
                        f"conf={row.confidence if row.confidence is not None else '-'}"
                    )
                self.stdout.write(
                    self.style.NOTICE(
                        "Dry run: no model calls, nothing saved."
                    )
                )
                return

            if not fresh and not sealed:
                self.stdout.write("Nothing to audit.")
                return

            self._run(fresh, sealed, opts)
        finally:
            lock.release()

    def _run(self, fresh, sealed, opts):
        service = AuditorService(check_summaries=not opts["skip_summaries"])

        run = AuditorRun.objects.create(
            taxonomy_version=TAXONOMY_VERSION,
            auditor_prompt_version=service.prompt_version,
            auditor_model_name=service.model_label,
            sampled_count=len(fresh) + len(sealed),
        )

        errors = 0
        try:
            fresh_samples = self._audit_group(service, run, fresh, False)
            errors += fresh_samples["errors"]
            sealed_samples = self._audit_group(service, run, sealed, True)
            errors += sealed_samples["errors"]

            run.classification_checked = sum(
                1 for s in fresh_samples["rows"] + sealed_samples["rows"]
                if s.category_agreed is not None
            )
            run.summary_checked = sum(
                1 for s in fresh_samples["rows"] + sealed_samples["rows"]
                if s.summary_claims_total
            )
            run.error_count = errors

            today = timezone.localdate()
            start = today - datetime.timedelta(days=opts["days"])

            windows = []
            if fresh_samples["rows"]:
                windows += scoring.write_windows(
                    run, fresh_samples["rows"],
                    period_start=start, period_end=today,
                )
            if sealed_samples["rows"]:
                windows += scoring.write_windows(
                    run, sealed_samples["rows"],
                    period_start=start, period_end=today, is_sealed=True,
                )

            for message in scoring.alarm_messages(windows):
                self.stdout.write(self.style.WARNING(f"ALARM  {message}"))

            if errors and errors == run.sampled_count:
                run.status = AuditorRun.Status.FAILED
            elif errors:
                run.status = AuditorRun.Status.PARTIAL
            else:
                run.status = AuditorRun.Status.COMPLETED

            self._print_summary(
                fresh_samples["rows"], sealed_samples["rows"]
            )

        except BaseException as exc:
            run.status = AuditorRun.Status.FAILED
            run.error_message = f"{exc!r}"[:2000]
            raise
        finally:
            run.completed_at = timezone.now()
            run.save()

    def _audit_group(self, service, run, rows, is_sealed):
        samples, errors = [], 0
        for classification in rows:
            try:
                samples.append(
                    service.audit(
                        classification, run=run, is_sealed=is_sealed
                    )
                )
            except GenerationServiceError as exc:
                errors += 1
                self.stderr.write(
                    f"  model unavailable for {classification.id}: {exc}"
                )
        return {"rows": samples, "errors": errors}

    def _print_summary(self, fresh, sealed):
        for label, rows in (("FRESH", fresh), ("SEALED", sealed)):
            if not rows:
                continue
            per_category, overall = scoring.summarise(rows)
            self.stdout.write("")
            self.stdout.write(f"{label}")
            for row in per_category:
                rate = row["agreement_rate"]
                self.stdout.write(
                    f"  {row['category']:<18} "
                    f"{'-' if rate is None else f'{rate:6.1%}'}  "
                    f"(n={row['sample_size']})"
                )
            rate = overall["agreement_rate"]
            self.stdout.write(
                f"  {'OVERALL':<18} "
                f"{'-' if rate is None else f'{rate:6.1%}'}  "
                f"(n={overall['sample_size']})"
            )
            if overall["grounding_rate"] is not None:
                self.stdout.write(
                    f"  {'GROUNDING':<18} {overall['grounding_rate']:6.1%}"
                )

            bands = scoring.calibration(rows)
            self.stdout.write("  calibration:")
            for band in bands:
                rate = band["agreement_rate"]
                self.stdout.write(
                    f"    conf {band['band']:<5} "
                    f"{'-' if rate is None else f'{rate:6.1%}'}  "
                    f"(n={band['sample_size']})"
                )
            if not scoring.is_monotonic(bands):
                self.stdout.write(
                    self.style.WARNING(
                        "    agreement does not rise with confidence - the "
                        "confidence score is not informative."
                    )
                )

            matrix = scoring.confusion(rows)
            if matrix:
                self.stdout.write("  disagreements:")
                for (predicted, actual), count in list(matrix.items())[:5]:
                    self.stdout.write(
                        f"    {predicted} -> {actual}  x{count}"
                    )

    def _report(self, days):
        since = timezone.localdate() - datetime.timedelta(days=days)
        windows = (
            AuditWindow.objects
            .filter(period_end__gte=since)
            .order_by("-period_end", "is_sealed", "category")
        )
        if not windows:
            self.stdout.write("No audit windows in that period.")
            return

        for window in windows:
            flag = " BELOW BAR" if window.below_bar else ""
            stream = "sealed" if window.is_sealed else "fresh "
            self.stdout.write(
                f"{window.period_end}  {stream}  "
                f"{window.category or 'OVERALL':<18} "
                f"{window.agreement_rate:6.1%}  "
                f"(n={window.sample_size}){flag}"
            )
