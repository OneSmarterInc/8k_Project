"""
Guide 4.2: draw the filings interns will label.

    python manage.py build_labelling_sample --size 500 --double 100
    python manage.py build_labelling_sample --size 500 --double 100 --dry-run

Random, stratified by primary item code, with a floor per stratum so
rare items are represented. `--double` marks that many for two labellers
(guide: 100 of 500, for kappa). Re-running appends new filings after the
existing sample; existing rows are never changed.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from watcher.interpreter.labelling import build_sample, eligible_filings, primary_stratum


class Command(BaseCommand):
    help = "Draw a stratified random sample of 8-K filings for labelling."

    def add_arguments(self, parser):
        parser.add_argument("--size", type=int, default=500)
        parser.add_argument("--double", type=int, default=100)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--min-per-stratum", type=int, default=5)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be drawn, write nothing.",
        )

    def handle(self, *args, **opts):
        if opts["size"] <= 0:
            raise CommandError("--size must be positive.")
        if opts["double"] < 0 or opts["double"] > opts["size"]:
            raise CommandError("--double must be between 0 and --size.")

        pool = Counter(
            primary_stratum(f.sec_item_codes, f.parsed_item_codes)
            for f in eligible_filings()
        )
        self.stdout.write(
            f"Eligible 8-K / 8-K/A filings with text: {sum(pool.values())}"
        )

        missing = pool.get("", 0)
        if missing:
            self.stdout.write(self.style.WARNING(
                f"{missing} eligible filing(s) have no item codes at all; "
                "they are sampled under '(none)'."
            ))

        if not pool:
            raise CommandError(
                "Nothing to sample. Filings need to be captured and "
                "ingested (chunked) first."
            )

        with transaction.atomic():
            rows = build_sample(
                size=opts["size"],
                double=opts["double"],
                seed=opts["seed"],
                min_per_stratum=opts["min_per_stratum"],
            )

            drawn = Counter(r.stratum or "(none)" for r in rows)
            doubles = sum(1 for r in rows if r.required_labels == 2)

            self.stdout.write(f"Drawn: {len(rows)}  (double-labelled: {doubles})")
            for stratum, n in sorted(drawn.items()):
                self.stdout.write(
                    f"  {stratum:<8} {n:>4} of {pool.get(stratum if stratum != '(none)' else '', 0)}"
                )

            if len(rows) < opts["size"]:
                self.stdout.write(self.style.WARNING(
                    f"Only {len(rows)} eligible filings; asked for {opts['size']}."
                ))

            if opts["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.NOTICE("Dry run: nothing saved."))
            else:
                self.stdout.write(self.style.SUCCESS("Sample saved."))
