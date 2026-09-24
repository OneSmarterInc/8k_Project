import os
import sys
import subprocess

from unittest.mock import MagicMock, patch

from django.test import TestCase

from watcher.services.watcher_launcher import (
    SubprocessWatcherLauncher,
)


class WatcherLauncherTestCase(TestCase):

    @patch(
        "watcher.services.watcher_launcher.reconcile_stale_running_runs"
    )
    @patch(
        "watcher.services.watcher_launcher.subprocess.Popen"
    )
    def test_launcher_success(
        self,
        mock_popen,
        mock_is_running,
    ):
        """
        Existing launcher behaviour.

        - Launch when watcher is not running.
        - Refuse duplicate launch when watcher is already running.
        """

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        # First launch: no watcher running
        mock_is_running.return_value = False

        launched = SubprocessWatcherLauncher.launch()

        self.assertTrue(launched)
        mock_popen.assert_called_once()


        # Second launch: watcher already running
        mock_popen.reset_mock()
        mock_is_running.return_value = True

        launched = SubprocessWatcherLauncher.launch()

        self.assertFalse(launched)
        mock_popen.assert_not_called()


    @patch(
        "watcher.services.watcher_launcher.reconcile_stale_running_runs"
    )
    @patch(
        "watcher.services.watcher_launcher.subprocess.Popen"
    )
    def test_launcher_is_detached_and_does_not_use_stdout_pipe(
        self,
        mock_popen,
        mock_is_running,
    ):
        """
        W-023 regression test.

        The watcher must not depend on a PIPE reader owned by Django.
        """

        mock_is_running.return_value = False

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process


        launched = SubprocessWatcherLauncher.launch()

        self.assertTrue(launched)

        mock_popen.assert_called_once()

        args, kwargs = mock_popen.call_args


        self.assertEqual(
            args[0],
            [
                sys.executable,
                "-u",
                "manage.py",
                "watcher",
                "--auto-index",
            ],
        )


        self.assertIn(
            "stdout",
            kwargs,
        )

        self.assertIsNot(
            kwargs["stdout"],
            subprocess.PIPE,
        )


        self.assertEqual(
            kwargs["stderr"],
            subprocess.STDOUT,
        )


        self.assertEqual(
            kwargs["stdin"],
            subprocess.DEVNULL,
        )


        self.assertTrue(
            kwargs["close_fds"]
        )


        if os.name == "nt":

            # W-023: CREATE_NO_WINDOW (hidden console, inherited by the
            # venv's real python.exe) + CREATE_NEW_PROCESS_GROUP
            # (independent of Django's Ctrl+C).
            expected_flags = (
                subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )

            self.assertEqual(
                kwargs["creationflags"],
                expected_flags,
            )

            # DETACHED_PROCESS must never come back: combined with the
            # venv launcher it opens a visible cmd window, and closing
            # that window kills the watcher.
            self.assertEqual(
                kwargs["creationflags"]
                & subprocess.DETACHED_PROCESS,
                0,
            )

            self.assertNotIn(
                "start_new_session",
                kwargs,
            )
        else:

            self.assertTrue(
                kwargs["start_new_session"]
            )

            self.assertNotIn(
                "creationflags",
                kwargs,
            )
            # ----------------------------------------------------------------------
# W-032: advisory-lock lifecycle tests (real PostgreSQL, no mocks)
#
# The lock is held from a SECOND connection because PostgreSQL
# advisory locks are re-entrant within one session.
# ----------------------------------------------------------------------

from django.db import connections
from django.test import TransactionTestCase

from watcher.models import AutomationRun
from watcher.services.watcher_lock import WATCHER_LOCK_ID

class _CursorSession:
    """Small wrapper so tests can call .execute() and .close()."""

    def __init__(self, wrapper):
        self.wrapper = wrapper

    def execute(self, sql, params=None):
        with self.wrapper.cursor() as cursor:
            cursor.execute(sql, params)

    def close(self):
        self.wrapper.close()

class WatcherLockLifecycleTests(TransactionTestCase):

    def _second_connection(self):
        """
        Open a separate session on the same (test) database.

        connections.create_connection() builds a brand-new Django
        connection object, so this works with psycopg2 or psycopg 3.
        """
        other = connections.create_connection("default")
        other.ensure_connection()
        other.set_autocommit(True)
        return _CursorSession(other)

    def _running_count(self):
        return AutomationRun.objects.filter(
            status=AutomationRun.Status.RUNNING
        ).count()

    def test_live_run_is_kept_when_lock_is_held(self):
        AutomationRun.objects.create(status=AutomationRun.Status.RUNNING)
        other = self._second_connection()
        try:
            other.execute("SELECT pg_advisory_lock(%s)", [WATCHER_LOCK_ID])
            self.assertTrue(SubprocessWatcherLauncher.is_running())
            self.assertEqual(self._running_count(), 1)
        finally:
            other.execute("SELECT pg_advisory_unlock(%s)", [WATCHER_LOCK_ID])
            other.close()

    def test_stale_run_is_reaped_when_lock_is_free(self):
        AutomationRun.objects.create(status=AutomationRun.Status.RUNNING)
        self.assertFalse(SubprocessWatcherLauncher.is_running())
        self.assertEqual(self._running_count(), 0)
        reaped = AutomationRun.objects.get()
        self.assertEqual(reaped.status, AutomationRun.Status.FAILED)
        self.assertIsNotNone(reaped.completed_at)

    @patch("watcher.services.watcher_launcher.subprocess.Popen")
    def test_launch_refused_while_lock_is_held(self, mock_popen):
        other = self._second_connection()
        try:
            other.execute("SELECT pg_advisory_lock(%s)", [WATCHER_LOCK_ID])
            self.assertFalse(SubprocessWatcherLauncher.launch())
            mock_popen.assert_not_called()
        finally:
            other.execute("SELECT pg_advisory_unlock(%s)", [WATCHER_LOCK_ID])
            other.close()

    @patch("watcher.services.watcher_launcher.subprocess.Popen")
    def test_launch_proceeds_after_stale_run_is_reaped(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=4321)
        AutomationRun.objects.create(status=AutomationRun.Status.RUNNING)
        self.assertTrue(SubprocessWatcherLauncher.launch())
        mock_popen.assert_called_once()
        self.assertEqual(self._running_count(), 0)