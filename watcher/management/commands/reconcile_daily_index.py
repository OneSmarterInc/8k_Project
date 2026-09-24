"""
W-002: reconcile captured 8-K / 8-K/A filings against EDGAR's daily index.

Read-only: it fetches EDGAR's master daily index and compares it with the
Filing table. It never modifies filings, runs, or any other data.

Usage:
    python manage.py reconcile_daily_index                  # previous weekday
    python manage.py reconcile_daily_index --date 2026-09-23
    python manage.py reconcile_daily_index --date 2026-09-25 --days 5

Output:
    - a per-day table in the console
    - reports/reconciliation/reconciliation_<first>_<last>.csv  (per-day summary)
    - reports/reconciliation/gaps_<first>_<last>.csv            (every missing/extra accession)

EDGAR publishes each daily index after the close (usually late evening ET),
so run this the next morning.
"""

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from watcher.models import Company, Filing
from watcher.services.sec_client import SECClient

RECONCILED_FORMS = ("8-K", "8-K/A")
DAILY_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/"
    "{year}/QTR{quarter}/master.{stamp}.idx"
)
MARKET_TZ = ZoneInfo("America/New_York")


def parse_master_index(text, forms=RECONCILED_FORMS):
    """
    Parse EDGAR master.idx text.

    Data lines look like:
        63908|MCDONALDS CORP|8-K|20260923|edgar/data/63908/0000063908-26-000076.txt

    Returns a list of dicts: cik (int), company, form, accession.
    """
    rows = []
    in_data = False

    for line in text.splitlines():
        if not in_data:
            if line.startswith("-----"):
                in_data = True
            continue

        parts = line.split("|")
        if len(parts) != 5:
            continue

        cik, company, form, _date_filed, filename = (p.strip() for p in parts)

        if form not in forms or not cik.isdigit():
            continue

        accession = Path(filename).name
        if accession.endswith(".txt"):
            accession = accession[:-4]

        rows.append({
            "cik": int(cik),
            "company": company,
            "form": form,
            "accession": accession,
        })

    return rows


def previous_weekday(today):
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def weekdays_ending(last_day, count):
    days = []
    day = last_day
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


class Command(BaseCommand):
    help = "Compare captured 8-K / 8-K/A filings with EDGAR's daily index (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            help="Last day to reconcile (YYYY-MM-DD). Default: previous weekday (ET).",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="Number of weekdays to reconcile, ending at --date (default: 1).",
        )
        parser.add_argument(
            "--out-dir",
            default=str(Path(settings.BASE_DIR) / "reports" / "reconciliation"),
            help="Folder for the CSV reports.",
        )

    def handle(self, *args, **options):
        if options["days"] < 1:
            raise CommandError("--days must be at least 1.")

        if options["date"]:
            try:
                last_day = date.fromisoformat(options["date"])
            except ValueError as exc:
                raise CommandError("--date must be YYYY-MM-DD.") from exc
        else:
            last_day = previous_weekday(datetime.now(MARKET_TZ).date())

        days = weekdays_ending(last_day, options["days"])

        universe = self._universe_ciks()
        if not universe:
            raise CommandError("No companies in the database; nothing to reconcile.")

        client = SECClient(verbose=False)

        summary_rows = []
        gap_rows = []

        for day in days:
            summary, gaps = self._reconcile_day(client, day, universe)
            summary_rows.append(summary)
            gap_rows.extend(gaps)
            self._print_day(summary)

        self._write_reports(Path(options["out_dir"]), days, summary_rows, gap_rows)

    # ------------------------------------------------------------------

    def _universe_ciks(self):
        ciks = set()
        for cik in Company.objects.values_list("cik", flat=True):
            cik = (cik or "").strip()
            if cik.isdigit():
                ciks.add(int(cik))
        return ciks

    def _fetch_index(self, client, day):
        url = DAILY_INDEX_URL.format(
            year=day.year,
            quarter=(day.month - 1) // 3 + 1,
            stamp=day.strftime("%Y%m%d"),
        )
        response = client.get(url, allow_status=(403, 404))
        if response.status_code != 200:
            return None
        return response.text

    def _reconcile_day(self, client, day, universe):
        text = self._fetch_index(client, day)

        if text is None:
            return {
                "date": day.isoformat(),
                "edgar_index": "NOT AVAILABLE",
                "edgar_count": "",
                "captured": "",
                "missing": "",
                "extra": "",
                "result": "NO INDEX (holiday, weekend, or not yet published)",
            }, []

        edgar = {
            row["accession"]: row
            for row in parse_master_index(text)
            if row["cik"] in universe
        }

        captured_all = set(
            Filing.objects
            .filter(form__in=RECONCILED_FORMS, accession_number__in=list(edgar))
            .values_list("accession_number", flat=True)
        )

        same_day_db = dict(
            Filing.objects
            .filter(form__in=RECONCILED_FORMS, filing_date=day)
            .values_list("accession_number", "company__ticker")
        )

        missing = sorted(set(edgar) - captured_all)
        extra = sorted(set(same_day_db) - set(edgar))

        gaps = [
            {
                "date": day.isoformat(),
                "type": "MISSING",
                "accession": acc,
                "cik": edgar[acc]["cik"],
                "company_or_ticker": edgar[acc]["company"],
                "form": edgar[acc]["form"],
                "cause": "",
            }
            for acc in missing
        ] + [
            {
                "date": day.isoformat(),
                "type": "EXTRA",
                "accession": acc,
                "cik": "",
                "company_or_ticker": same_day_db[acc],
                "form": "",
                "cause": "",
            }
            for acc in extra
        ]

        return {
            "date": day.isoformat(),
            "edgar_index": "OK",
            "edgar_count": len(edgar),
            "captured": len(edgar) - len(missing),
            "missing": len(missing),
            "extra": len(extra),
            "result": "MATCH" if not missing and not extra else "GAP",
        }, gaps

    def _print_day(self, s):
        if s["edgar_index"] != "OK":
            self.stdout.write(f"{s['date']}  {s['result']}")
            return

        line = (
            f"{s['date']}  EDGAR {s['edgar_count']:>4}  "
            f"captured {s['captured']:>4}  missing {s['missing']:>3}  "
            f"extra {s['extra']:>3}  {s['result']}"
        )
        style = self.style.SUCCESS if s["result"] == "MATCH" else self.style.WARNING
        self.stdout.write(style(line))

    def _write_reports(self, out_dir, days, summary_rows, gap_rows):
        out_dir.mkdir(parents=True, exist_ok=True)
        span = f"{days[0]:%Y%m%d}_{days[-1]:%Y%m%d}"

        summary_path = out_dir / f"reconciliation_{span}.csv"
        with summary_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(summary_rows[0]))
            writer.writeheader()
            writer.writerows(summary_rows)

        gaps_path = out_dir / f"gaps_{span}.csv"
        with gaps_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["date", "type", "accession", "cik",
                            "company_or_ticker", "form", "cause"],
            )
            writer.writeheader()
            writer.writerows(gap_rows)

        self.stdout.write(f"Summary: {summary_path}")
        self.stdout.write(f"Gaps:    {gaps_path} ({len(gap_rows)} row(s); fill in 'cause' for each)")