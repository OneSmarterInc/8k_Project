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