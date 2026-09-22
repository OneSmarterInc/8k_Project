import os
import sys
import subprocess

from datetime import datetime, time
from unittest.mock import MagicMock, patch

from django.test import TestCase

from watcher.models import ScheduleConfig
from watcher.services.watcher_launcher import (
    SubprocessWatcherLauncher,
)
from watcher.services.schedule_manager import (
    build_triggers_for_config,
)
from watcher.management.commands.run_scheduler import (
    Command as SchedulerCommand,
)


class SchedulerTestCase(TestCase):

    def setUp(self):
        self.config = ScheduleConfig.objects.create(
            frequency="daily",
            is_active=True,
            start_date=datetime(
                2026,
                9,
                21,
            ).date(),
            start_time=time(
                10,
                0,
            ),
            daily_recur=1,
            run_times=["10:00"],
        )

    @patch(
        "watcher.services.watcher_launcher."
        "reconcile_stale_running_runs"
    )
    @patch(
        "watcher.services.watcher_launcher."
        "subprocess.Popen"
    )
    def test_launcher_success(
        self,
        mock_popen,
        mock_reconcile,
    ):
        """
        W-024:
        Launch only when PostgreSQL lock state says no watcher
        is actually running.
        """

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        # No real watcher running.
        mock_reconcile.return_value = False

        launched = SubprocessWatcherLauncher.launch()

        self.assertTrue(launched)
        mock_popen.assert_called_once()

        # Real watcher running.
        mock_popen.reset_mock()
        mock_reconcile.return_value = True

        launched = SubprocessWatcherLauncher.launch()

        self.assertFalse(launched)
        mock_popen.assert_not_called()

    @patch(
        "watcher.services.watcher_launcher."
        "reconcile_stale_running_runs"
    )
    @patch(
        "watcher.services.watcher_launcher."
        "subprocess.Popen"
    )
    def test_launcher_uses_detached_process_without_stdout_pipe(
        self,
        mock_popen,
        mock_reconcile,
    ):
        """
        W-023 regression test.
        """

        mock_reconcile.return_value = False

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

    def test_explicit_timezone(self):
        triggers = build_triggers_for_config(
            self.config
        )

        self.assertEqual(
            len(triggers),
            1,
        )

        trigger = triggers[0]

        self.assertEqual(
            trigger.timezone.key,
            "America/New_York",
        )

    def test_config_change_detection(self):
        cmd = SchedulerCommand()

        old_config = ScheduleConfig.objects.get(
            id=self.config.id
        )

        new_config = ScheduleConfig.objects.get(
            id=self.config.id
        )

        self.assertFalse(
            cmd._config_changed(
                old_config,
                new_config,
            )
        )

        new_config.is_active = False

        self.assertTrue(
            cmd._config_changed(
                old_config,
                new_config,
            )
        )

        new_config.is_active = False
        new_config.save()

        new_config.is_active = True
        new_config.run_times = [
            "14:00"
        ]

        self.assertTrue(
            cmd._config_changed(
                old_config,
                new_config,
            )
        )