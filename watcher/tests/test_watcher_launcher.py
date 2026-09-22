import os
import sys
import subprocess

from unittest.mock import MagicMock, patch

from django.test import TestCase

from watcher.models import AutomationRun
from watcher.services.watcher_launcher import SubprocessWatcherLauncher


class WatcherLauncherTestCase(TestCase):

    @patch("watcher.services.watcher_launcher.subprocess.Popen")
    def test_launcher_success(self, mock_popen):
        """
        Existing launcher behaviour must remain unchanged.

        - Launch when no watcher is running.
        - Refuse duplicate launch when an AutomationRun is RUNNING.
        """

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        launched = SubprocessWatcherLauncher.launch()

        self.assertTrue(launched)
        mock_popen.assert_called_once()

        AutomationRun.objects.create(
            status=AutomationRun.Status.RUNNING
        )

        mock_popen.reset_mock()

        launched = SubprocessWatcherLauncher.launch()

        self.assertFalse(launched)
        mock_popen.assert_not_called()

    @patch("watcher.services.watcher_launcher.subprocess.Popen")
    def test_launcher_is_detached_and_does_not_use_stdout_pipe(
        self,
        mock_popen,
    ):
        """
        W-023 regression test.

        The watcher must not depend on a PIPE reader owned by Django.
        """

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        launched = SubprocessWatcherLauncher.launch()

        self.assertTrue(launched)

        mock_popen.assert_called_once()

        args, kwargs = mock_popen.call_args

        # Existing watcher command must remain unchanged.
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

        # W-023: stdout must go directly to the log file,
        # never through subprocess.PIPE.
        self.assertIn("stdout", kwargs)
        self.assertIsNot(
            kwargs["stdout"],
            subprocess.PIPE,
        )

        # stderr continues into the same watcher log.
        self.assertEqual(
            kwargs["stderr"],
            subprocess.STDOUT,
        )

        # Detached process must not wait for stdin.
        self.assertEqual(
            kwargs["stdin"],
            subprocess.DEVNULL,
        )

        self.assertTrue(
            kwargs["close_fds"]
        )

        # Verify platform-specific detachment.
        if os.name == "nt":
            expected_flags = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )

            self.assertEqual(
                kwargs["creationflags"],
                expected_flags,
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