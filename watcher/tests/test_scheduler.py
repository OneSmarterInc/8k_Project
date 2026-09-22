import os
import sys
import subprocess
import unittest

from unittest.mock import patch, MagicMock
from datetime import datetime, time

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from django.test import TestCase

from watcher.models import ScheduleConfig, AutomationRun
from watcher.services.watcher_launcher import SubprocessWatcherLauncher
from watcher.services.schedule_manager import build_triggers_for_config
from watcher.management.commands.run_scheduler import Command as SchedulerCommand


class SchedulerTestCase(TestCase):

    def setUp(self):
        """
        Create a default scheduler configuration used by the scheduler tests.
        """
        self.config = ScheduleConfig.objects.create(
            frequency="daily",
            is_active=True,
            start_date=datetime(2026, 9, 21).date(),
            start_time=time(10, 0),
            daily_recur=1,
            run_times=["10:00"],
        )

    @patch("watcher.services.watcher_launcher.subprocess.Popen")
    def test_launcher_success(self, mock_popen):
        """
        TEST 5:
        Watcher trigger fires and subprocess launcher is invoked.

        TEST 6:
        A watcher launch is prevented when an AutomationRun is already RUNNING.
        """

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        # Scenario 1:
        # No watcher is currently running, so launch should succeed.
        launched = SubprocessWatcherLauncher.launch()

        self.assertTrue(launched)
        mock_popen.assert_called_once()

        # Scenario 2:
        # An existing RUNNING AutomationRun should prevent another launch.
        AutomationRun.objects.create(
            status=AutomationRun.Status.RUNNING
        )

        mock_popen.reset_mock()

        launched = SubprocessWatcherLauncher.launch()

        self.assertFalse(launched)
        mock_popen.assert_not_called()

    @patch("watcher.services.watcher_launcher.subprocess.Popen")
    def test_launcher_uses_detached_process_without_stdout_pipe(
        self,
        mock_popen,
    ):
        """
        W-023 regression test.

        The watcher subprocess must not use stdout=subprocess.PIPE.

        Using PIPE would require the Django process to continuously read the
        subprocess output. If Django terminates, the watcher could eventually
        block when the pipe buffer fills.

        Instead, stdout must be redirected directly to the watcher log file and
        the subprocess must be detached from the Django process.
        """

        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        launched = SubprocessWatcherLauncher.launch()

        self.assertTrue(launched)
        mock_popen.assert_called_once()

        args, kwargs = mock_popen.call_args

        # Verify that the existing watcher command has not changed.
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

        # stdout must exist but must NOT use PIPE.
        self.assertIn("stdout", kwargs)
        self.assertIsNot(
            kwargs["stdout"],
            subprocess.PIPE,
        )

        # stderr should continue to be redirected to stdout so both are
        # captured in watcher_latest.log.
        self.assertEqual(
            kwargs["stderr"],
            subprocess.STDOUT,
        )

        # The detached watcher must not wait for terminal input.
        self.assertEqual(
            kwargs["stdin"],
            subprocess.DEVNULL,
        )

        self.assertTrue(
            kwargs["close_fds"]
        )

        # Verify operating-system-specific subprocess detachment.
        if os.name == "nt":
            expected_flags = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )

            self.assertEqual(
                kwargs["creationflags"],
                expected_flags,
            )
        else:
            self.assertTrue(
                kwargs["start_new_session"]
            )

    def test_explicit_timezone(self):
        """
        TEST 8:
        Scheduler triggers must explicitly use America/New_York.
        """

        triggers = build_triggers_for_config(self.config)

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
        """
        TEST 2, 3, 4:
        Scheduler detects relevant ScheduleConfig changes.
        """

        cmd = SchedulerCommand()

        old_config = ScheduleConfig.objects.get(
            id=self.config.id
        )

        new_config = ScheduleConfig.objects.get(
            id=self.config.id
        )

        # Same configuration should not be considered changed.
        self.assertFalse(
            cmd._config_changed(
                old_config,
                new_config,
            )
        )

        # Active -> Inactive should be detected.
        new_config.is_active = False

        self.assertTrue(
            cmd._config_changed(
                old_config,
                new_config,
            )
        )

        # Persist the inactive state.
        new_config.is_active = False
        new_config.save()

        # Changing run time should also be detected.
        new_config.is_active = True
        new_config.run_times = ["14:00"]

        self.assertTrue(
            cmd._config_changed(
                old_config,
                new_config,
            )
        )