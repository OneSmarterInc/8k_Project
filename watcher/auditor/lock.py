"""
Advisory lock for `manage.py audit`, copied from the Interpreter's
pattern in watcher/interpreter/lock.py with a DIFFERENT lock ID, so an
Auditor run, an Interpreter run and a Watcher run never block each
other.

    Watcher      82039147519301
    Interpreter  82039147519302
    Auditor      82039147519303   <- this module
"""

import logging

from django.db import connection
from django.utils import timezone

from watcher.models import AuditorRun


logger = logging.getLogger(__name__)

AUDITOR_LOCK_ID = 82039147519303


def _is_postgres():
    return connection.vendor == "postgresql"


def try_acquire():
    if not _is_postgres():
        return True
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [AUDITOR_LOCK_ID])
        return bool(cursor.fetchone()[0])


def release():
    if not _is_postgres():
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [AUDITOR_LOCK_ID])
    except Exception:
        logger.exception("Could not release the Auditor advisory lock.")


def reconcile_stale_runs():
    """
    Called only after this process holds the lock: any RUNNING
    AuditorRun left behind belongs to a process that died.
    Touches AuditorRun only - never InterpreterRun, never AutomationRun.
    """
    stale = AuditorRun.objects.filter(status=AuditorRun.Status.RUNNING)
    count = stale.update(
        status=AuditorRun.Status.FAILED,
        completed_at=timezone.now(),
        error_message="Process ended without finishing (found stale on next run).",
    )
    if count:
        logger.warning("Marked %s stale AuditorRun(s) as FAILED.", count)
    return count
