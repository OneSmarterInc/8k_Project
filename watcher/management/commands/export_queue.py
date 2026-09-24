"""
W-038: append-only queue file export.

Spec reference: R-07, R-13, RM-01.

The Phase 1 spec asks the Watcher to write one clean row per filing to a
queue file and never modify a row once written. Every field already
exists on the Filing table, but rows are updated in place, so the
database alone cannot show that a row changed after the fact.

This command writes that artifact. It is STRICTLY READ-ONLY: it opens no
transaction, saves no model, and touches no run. Nothing in the capture
path depends on it, so a failure here can never affect a watcher run.

Usage:
    python manage.py export_queue                      # previous market day
    python manage.py export_queue --date 2026-09-24
    python manage.py export_queue --date 2026-09-24 --dry-run
    python manage.py export_queue --days 5             # 5 days back from --date
    python manage.py export_queue --all-forms          # include 10-K / 10-Q

Output:
    queue/2026-09-24.csv

Revisions:
    An existing file is NEVER overwritten. A second export for the same
    date writes queue/2026-09-24.r2.csv, then .r3.csv, and logs a
    warning. A second revision is itself a signal worth seeing, which is
    the whole point of the artifact.

Columns (R-13, plus entry_rule so the file is self-describing about
which rule produced it):
    ticker, cik, accession, accepted_ts, filing_date, entry_session,
    items, url, flag, entry_rule

Day selection:
    A filing belongs to the day EDGAR accepted it, in market time. Rows
    with no acceptance timestamp (R-12 flags these rather than guessing)
    fall back to filing_date so a flagged row is never silently dropped
    from the queue.
"""

import csv
import logging

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from watcher.models import Filing

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("America/New_York")

QUEUE_FORMS = ("8-K", "8-K/A")
ALL_FORMS = ("8-K", "8-K/A", "10-K", "10-Q")

QUEUE_COLUMNS = (
    "ticker",
    "cik",
    "accession",
    "accepted_ts",
    "filing_date",
    "entry_session",
    "items",
    "url",
    "flag",
    "entry_rule",
)

MAX_REVISIONS = 50


def market_today():
    """Today in market time, not server time (W-025)."""
    return datetime.now(MARKET_TZ).date()


def previous_market_day(reference=None):
    """
    Previous calendar weekday. Deliberately NOT the trading calendar:
    the queue file is a capture artifact, and EDGAR disseminates on
    holidays that the market calendar excludes.
    """
    reference = reference or market_today()
    day = reference - timedelta(days=1)

    while day.weekday() >= 5:
        day -= timedelta(days=1)

    return day


def existing_revisions(queue_dir: Path, day: date):
    """Every file already written for `day`, oldest revision first."""
    base = queue_dir / f"{day.isoformat()}.csv"

    found = []

    if base.exists():
        found.append(base)

    for revision in range(2, MAX_REVISIONS + 1):
        candidate = queue_dir / f"{day.isoformat()}.r{revision}.csv"

        if candidate.exists():
            found.append(candidate)

    return found


def read_queue_rows(path: Path):
    """Read a previously written queue file back as a list of dicts."""
    with open(path, "r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def rows_match(existing_rows, new_rows):
    """
    True when a previously written file already says exactly this.

    Compared on the R-13 columns only, so a file written by an older
    build with extra columns does not read as changed.
    """
    if len(existing_rows) != len(new_rows):
        return False

    for old, new in zip(existing_rows, new_rows):
        for column in QUEUE_COLUMNS:
            if str(old.get(column, "")) != str(new.get(column, "")):
                return False

    return True


def resolve_output_path(queue_dir: Path, day: date):
    """
    Return the path to write, never overwriting an existing file.

    2026-09-24.csv -> 2026-09-24.r2.csv -> 2026-09-24.r3.csv
    Returns (path, revision).
    """
    base = queue_dir / f"{day.isoformat()}.csv"

    if not base.exists():
        return base, 1

    for revision in range(2, MAX_REVISIONS + 1):
        candidate = queue_dir / f"{day.isoformat()}.r{revision}.csv"

        if not candidate.exists():
            return candidate, revision

    raise CommandError(
        f"Refusing to export: {MAX_REVISIONS} revisions already exist "
        f"for {day.isoformat()}. Investigate before exporting again."
    )


def filings_for_day(day: date, forms):
    """
    Filings EDGAR accepted on `day` in market time.

    Rows with a null accepted_at (R-12 flags rather than guesses) fall
    back to filing_date so a flagged row still reaches the queue.
    """
    start = datetime(
        day.year,
        day.month,
        day.day,
        0,
        0,
        0,
        tzinfo=MARKET_TZ,
    )

    end = start + timedelta(days=1)

    return (
        Filing.objects.filter(form__in=forms)
        .filter(
            Q(accepted_at__gte=start, accepted_at__lt=end)
            | Q(accepted_at__isnull=True, filing_date=day)
        )
        .select_related("company")
        .order_by("accepted_at", "accession_number", "sequence")
    )


def row_for(filing):
    """One clean row per filing, in R-13 column order."""
    accepted_ts = ""

    if filing.accepted_at:
        accepted_ts = (
            filing.accepted_at
            .astimezone(MARKET_TZ)
            .isoformat()
        )

    return {
        "ticker": filing.company.ticker or "",
        "cik": filing.company.cik or "",
        "accession": filing.accession_number or "",
        "accepted_ts": accepted_ts,
        "filing_date": (
            filing.filing_date.isoformat()
            if filing.filing_date
            else ""
        ),
        "entry_session": (
            filing.entry_session.isoformat()
            if filing.entry_session
            else ""
        ),
        # R-04 treats the SEC cover-page items as authoritative.
        # parsed_item_codes and item_codes_match stay in the database;
        # the queue file carries what EDGAR reported.
        "items": filing.sec_item_codes or "",
        "url": filing.source_url or "",
        "flag": "true" if filing.flag else "false",
        "entry_rule": filing.entry_rule or "",
    }


def write_queue_file(path: Path, rows):
    """
    Write the file atomically: build a .tmp sibling, then rename. A
    crash mid-write leaves no half-written queue file behind.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with open(tmp_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(QUEUE_COLUMNS),
            lineterminator="\n",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)

    tmp_path.replace(path)


class Command(BaseCommand):
    help = (
        "W-038: export the append-only queue file for a given day "
        "(R-13 schema). Read-only; never overwrites an existing file."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="date",
            help=(
                "Market-time day to export, YYYY-MM-DD. "
                "Defaults to the previous weekday."
            ),
        )

        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help=(
                "Number of consecutive days to export, counting back "
                "from --date. Default 1."
            ),
        )

        parser.add_argument(
            "--all-forms",
            action="store_true",
            help=(
                "Include 10-K and 10-Q. Default is 8-K and 8-K/A, "
                "which is what the Phase 1 queue describes."
            ),
        )

        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Write a new revision even when the day's rows are "
                "unchanged since the last export."
            ),
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be written without writing it.",
        )

        parser.add_argument(
            "--empty-ok",
            action="store_true",
            help=(
                "Write a header-only file when a day has no filings. "
                "By default an empty day is skipped, so the absence of "
                "a file is itself readable as 'nothing captured'."
            ),
        )

    def handle(self, *args, **options):
        if options.get("days") is not None and options["days"] < 1:
            raise CommandError("--days must be 1 or greater.")

        raw_date = options.get("date")

        if raw_date:
            try:
                last_day = datetime.strptime(
                    raw_date,
                    "%Y-%m-%d",
                ).date()
            except ValueError:
                raise CommandError(
                    f"Invalid --date '{raw_date}'. Use YYYY-MM-DD."
                )
        else:
            last_day = previous_market_day()

        days = options.get("days") or 1

        forms = (
            ALL_FORMS
            if options.get("all_forms")
            else QUEUE_FORMS
        )

        force = bool(options.get("force"))
        dry_run = bool(options.get("dry_run"))
        empty_ok = bool(options.get("empty_ok"))

        queue_dir = Path(settings.BASE_DIR) / "queue"

        if not dry_run:
            queue_dir.mkdir(parents=True, exist_ok=True)

        written = []

        for offset in range(days - 1, -1, -1):
            day = last_day - timedelta(days=offset)

            rows = [
                row_for(filing)
                for filing in filings_for_day(day, forms)
            ]

            if not rows and not empty_ok:
                self.stdout.write(
                    f"{day.isoformat()}: no filings, skipped."
                )
                continue

            # A nightly sweep re-exports a day it already exported.
            # Writing a revision every night would make revisions
            # meaningless. Only write one when the day's rows actually
            # differ from the newest file already on disk - which is
            # precisely the silent-edit signal R-07 asks for.
            prior = existing_revisions(queue_dir, day)

            if prior and not force:
                try:
                    if rows_match(read_queue_rows(prior[-1]), rows):
                        self.stdout.write(
                            f"{day.isoformat()}: unchanged since "
                            f"{prior[-1].name}, nothing written."
                        )
                        continue

                except OSError as read_exc:
                    # Unreadable prior file: fall through and write a
                    # revision rather than silently skipping the day.
                    logger.warning(
                        "W-038: could not read %s (%s); writing a new "
                        "revision.",
                        prior[-1].name,
                        read_exc,
                    )

            path, revision = resolve_output_path(queue_dir, day)

            if revision > 1:
                message = (
                    f"W-038: a queue file for {day.isoformat()} "
                    f"already exists. Writing revision {revision} "
                    f"({path.name}) rather than overwriting."
                )

                logger.warning(message)

                self.stdout.write(
                    self.style.WARNING(message)
                )

            if dry_run:
                self.stdout.write(
                    f"{day.isoformat()}: would write {len(rows)} "
                    f"row(s) to {path.name}"
                )
                continue

            write_queue_file(path, rows)

            written.append(path)

            flagged = sum(
                1
                for row in rows
                if row["flag"] == "true"
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"{day.isoformat()}: wrote {len(rows)} row(s) "
                    f"to {path.name}"
                    + (
                        f" ({flagged} flagged)"
                        if flagged
                        else ""
                    )
                )
            )

        if not dry_run and not written:
            self.stdout.write("Nothing written.")

        return None