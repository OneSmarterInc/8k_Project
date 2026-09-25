"""Capture queue exports (W-038 artifacts): list by date range, download.

The nightly sweep writes one CSV per market day into `queue/` via the
`export_queue` command. This module lets the Filings page list those
files across a date range and download any of them, so an analyst never
needs shell access to the server.

STRICTLY READ-ONLY. Nothing here writes, regenerates or deletes a queue
file. Regeneration stays with the management command, so a browser
request can never produce a revision and undermine W-038's append-only
guarantee.

Security:

- The download filename is matched against QUEUE_FILE_RE, so only
  "2026-09-24.csv" and "2026-09-24.r2.csv" shapes are accepted.
- The resolved path is then re-checked to sit inside the queue
  directory, which defeats traversal through a symlink the regex
  cannot see.
- Admin only, matching the rest of the write-capable API surface.
"""

import csv
import re

from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404

from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from watcher.management.commands.export_queue import market_today

# 2026-09-24.csv  or  2026-09-24.r2.csv
QUEUE_FILE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?:\.r(?P<revision>\d+))?\.csv$"
)

# Default window when the caller supplies no dates.
DEFAULT_RANGE_DAYS = 30

# Hard ceiling so a very wide range cannot stat thousands of files.
MAX_RANGE_DAYS = 400

MAX_LISTED_FILES = 500


def queue_dir():
    return Path(settings.BASE_DIR) / "queue"


def parse_date(raw, fallback):
    if not raw:
        return fallback

    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def describe(path: Path):
    """One listing row. Never raises on a malformed or unreadable file."""
    match = QUEUE_FILE_RE.match(path.name)

    if not match:
        return None

    try:
        size = path.stat().st_size
    except OSError:
        return None

    rows = None

    try:
        with open(path, newline="", encoding="utf-8") as handle:
            # Subtract the header. Counting is cheap: one row per
            # filing for a single market day.
            rows = max(sum(1 for _ in csv.reader(handle)) - 1, 0)
    except OSError:
        pass

    return {
        "filename": path.name,
        "date": match.group("date"),
        "revision": int(match.group("revision") or 1),
        "rows": rows,
        "size_bytes": size,
    }


@api_view(["GET"])
@permission_classes([IsAdminUser])
def queue_exports(request):
    """
    GET /api/queue/exports/?start=YYYY-MM-DD&end=YYYY-MM-DD

    Both bounds are inclusive and optional. Omitting them returns the
    last DEFAULT_RANGE_DAYS days ending today (market time).

    Always 200. An empty `files` list means nothing was exported in that
    range, which the UI should present as a normal state rather than an
    error - on most of a trading day the sweep has not run yet.
    """
    today = market_today()

    end = parse_date(
        request.GET.get("end"),
        today,
    )
    start = parse_date(
        request.GET.get("start"),
        today - timedelta(days=DEFAULT_RANGE_DAYS - 1),
    )

    if start is None or end is None:
        return Response(
            {"detail": "Dates must be YYYY-MM-DD."},
            status=400,
        )

    if start > end:
        return Response(
            {"detail": "start must not be after end."},
            status=400,
        )

    if (end - start).days + 1 > MAX_RANGE_DAYS:
        return Response(
            {
                "detail": (
                    f"Range too wide; {MAX_RANGE_DAYS} days maximum."
                )
            },
            status=400,
        )

    directory = queue_dir()

    payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "files": [],
    }

    if not directory.is_dir():
        return Response(payload)

    described = []

    for path in directory.iterdir():
        if not path.is_file():
            continue

        row = describe(path)

        if not row:
            continue

        if start.isoformat() <= row["date"] <= end.isoformat():
            described.append(row)

    # Newest day first, and within a day the newest revision first.
    described.sort(
        key=lambda row: (row["date"], row["revision"]),
        reverse=True,
    )

    payload["files"] = described[:MAX_LISTED_FILES]
    payload["total_rows"] = sum(
        row["rows"] or 0 for row in payload["files"]
    )

    return Response(payload)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def queue_export_download(request, filename):
    """
    GET /api/queue/exports/<filename>/

    Streams the CSV back as an attachment.
    """
    if not QUEUE_FILE_RE.match(filename):
        raise Http404("Not a queue export.")

    directory = queue_dir().resolve()

    try:
        path = (directory / filename).resolve()
    except OSError:
        raise Http404("Not a queue export.")

    # Second gate: the resolved path must still sit inside queue/.
    if directory not in path.parents or not path.is_file():
        raise Http404("Queue export not found.")

    return FileResponse(
        open(path, "rb"),
        as_attachment=True,
        filename=filename,
        content_type="text/csv",
    )