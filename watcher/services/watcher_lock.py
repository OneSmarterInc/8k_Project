import logging

from django.db import connection
from django.utils import timezone

from watcher.models import AutomationRun


logger = logging.getLogger(__name__)


# Shared PostgreSQL advisory lock ID used by the SEC watcher.
WATCHER_LOCK_ID = 82039147519301


def is_watcher_lock_held():
    """
    Return True when another database session currently owns the watcher
    advisory lock.

    If the lock is available, acquire and immediately release it only for the
    purpose of checking its state.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s)",
            [WATCHER_LOCK_ID],
        )

        acquired = cursor.fetchone()[0]

        if acquired:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s)",
                [WATCHER_LOCK_ID],
            )
            return False

        return True


def reconcile_stale_running_runs():
    """
    Reconcile AutomationRun rows against the PostgreSQL advisory lock.

    PostgreSQL advisory lock = actual watcher process lifecycle truth.

    If the lock is held:
        A watcher is really running. Do not modify RUNNING rows.

    If the lock is free:
        Any RUNNING rows are stale leftovers from a process that died before
        its normal finalization code executed.

    Returns True only when a watcher is actually running.
    """
    try:
        lock_held = is_watcher_lock_held()
    except Exception:
        # Fail safe:
        # if PostgreSQL lock state cannot be determined, do NOT alter any
        # AutomationRun records.
        logger.exception(
            "Unable to determine SEC watcher advisory-lock state."
        )

        return AutomationRun.objects.filter(
            status=AutomationRun.Status.RUNNING
        ).exists()

    if lock_held:
        return True

    stale_runs = AutomationRun.objects.filter(
        status=AutomationRun.Status.RUNNING
    )

    stale_count = stale_runs.count()

    if stale_count:
        stale_runs.update(
            status=AutomationRun.Status.FAILED,
            completed_at=timezone.now(),
        )

        logger.warning(
            "Marked %s stale AutomationRun(s) as FAILED because "
            "the PostgreSQL watcher advisory lock is not held.",
            stale_count,
        )

    return False