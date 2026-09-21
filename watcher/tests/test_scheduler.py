import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, time
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from django.test import TestCase
from django.conf import settings
from watcher.models import ScheduleConfig, AutomationRun
from watcher.services.watcher_launcher import SubprocessWatcherLauncher
from watcher.services.schedule_manager import build_triggers_for_config
from watcher.management.commands.run_scheduler import Command as SchedulerCommand

class SchedulerTestCase(TestCase):
    def setUp(self):
        # Create a mock ScheduleConfig
        self.config = ScheduleConfig.objects.create(
            frequency="daily",
            is_active=True,
            start_date=datetime(2026, 9, 21).date(),
            start_time=time(10, 0),
            daily_recur=1,
            run_times=["10:00"]
        )

    @patch("watcher.services.watcher_launcher.subprocess.Popen")
    def test_launcher_success(self, mock_popen):
        # TEST 5: Watcher trigger fires, launcher invoked
        # TEST 6: Watcher already running is prevented
        
        # Scenario 1: Not running, should launch
        launched = SubprocessWatcherLauncher.launch()
        self.assertTrue(launched)
        mock_popen.assert_called_once()
        
        # Scenario 2: Already running
        AutomationRun.objects.create(status=AutomationRun.Status.RUNNING)
        mock_popen.reset_mock()
        launched = SubprocessWatcherLauncher.launch()
        self.assertFalse(launched)
        mock_popen.assert_not_called()
        
    def test_explicit_timezone(self):
        # TEST 8: Eastern Time explicitly used
        triggers = build_triggers_for_config(self.config)
        self.assertEqual(len(triggers), 1)
        trigger = triggers[0]
        
        # Validate that the trigger timezone is America/New_York
        self.assertEqual(trigger.timezone.key, "America/New_York")
        
    def test_config_change_detection(self):
        # TEST 2, 3, 4: Scheduler detects changes
        cmd = SchedulerCommand()
        
        old_config = ScheduleConfig.objects.get(id=self.config.id)
        new_config = ScheduleConfig.objects.get(id=self.config.id)
        
        # Same config should be False
        self.assertFalse(cmd._config_changed(old_config, new_config))
        
        # Changed config should be True
        new_config.is_active = False
        self.assertTrue(cmd._config_changed(old_config, new_config))
        
        # Active -> Inactive
        new_config.is_active = False
        new_config.save()
        
        # Time change
        new_config.is_active = True
        new_config.run_times = ["14:00"]
        self.assertTrue(cmd._config_changed(old_config, new_config))

