"""
Advisory lock for `manage.py interpret`, copied from the Watcher's
pattern in watcher/services/watcher_lock.py with a DIFFERENT lock ID, so
an Interpreter run and a Watcher run never block each other.
"""

import logging

from django.db import connection
from django.utils import timezone

from watcher.models import InterpreterRun


logger = logging.getLogger(__name__)

INTERPRETER_LOCK_ID = 82039147519302   # Watcher uses ...301


def _is_postgres():
    return connection.vendor == "postgresql"


def try_acquire():
    if not _is_postgres():
        return True
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [INTERPRETER_LOCK_ID])
        return bool(cursor.fetchone()[0])


def release():
    if not _is_postgres():
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [INTERPRETER_LOCK_ID])
    except Exception:
        logger.exception("Could not release the Interpreter advisory lock.")


def reconcile_stale_runs():
    """
    Called only after this process holds the lock: any RUNNING
    InterpreterRun left behind belongs to a process that died.
    Touches InterpreterRun only, never AutomationRun.
    """
    stale = InterpreterRun.objects.filter(status=InterpreterRun.Status.RUNNING)
    count = stale.update(
        status=InterpreterRun.Status.FAILED,
        completed_at=timezone.now(),
        error_message="Process ended without finishing (found stale on next run).",
    )
    if count:
        logger.warning("Marked %s stale InterpreterRun(s) as FAILED.", count)
    return count
