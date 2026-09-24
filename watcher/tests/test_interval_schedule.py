"""
W-037 (R-08 / R-09): intraday interval polling and nightly catch-up sweep.

Half of this file is regression cover. The tests named
test_regression_* assert that no pre-existing frequency changed
behaviour, because that is the actual risk in this change.
"""

from datetime import date, time
from unittest.mock import MagicMock, patch

from django.test import TestCase

from watcher.models import ScheduleConfig
from watcher.services.schedule_manager import (
    build_interval_jobs,
    build_jobs_for_config,
    build_triggers_for_config,
    normalize_interval_minutes,
)
from watcher.services.watcher_launcher import (
    SubprocessWatcherLauncher,
)


def cron_fields(trigger):
    """Flatten an APScheduler CronTrigger into {field_name: expression}."""
    return {
        str(field.name): str(field)
        for field in trigger.fields
    }


class IntervalScheduleTestCase(TestCase):

    def setUp(self):
        self.config = ScheduleConfig.objects.create(
            frequency="interval",
            is_active=True,
            start_date=date(2026, 9, 21),
            start_time=time(10, 0),
            run_times=["10:00"],
            interval_minutes=20,
            active_window_start=time(6, 0),
            active_window_end=time(21, 0),
            nightly_sweep=True,
        )

    # ------------------------------------------------------------------
    # New behaviour
    # ------------------------------------------------------------------

    def test_interval_frequency_builds_a_weekday_cron_trigger(self):
        jobs = build_jobs_for_config(self.config)

        trigger, kwargs = jobs[0]
        fields = cron_fields(trigger)

        self.assertEqual(fields["day_of_week"], "mon-fri")
        self.assertEqual(fields["hour"], "6-21")
        self.assertEqual(fields["minute"], "*/20")
        self.assertEqual(kwargs, {"sweep": False})
        self.assertEqual(
            trigger.timezone.key,
            "America/New_York",
        )

    def test_nightly_sweep_trigger_is_added_for_interval(self):
        jobs = build_jobs_for_config(self.config)

        self.assertEqual(len(jobs), 2)

        trigger, kwargs = jobs[1]
        fields = cron_fields(trigger)

        self.assertEqual(fields["hour"], "22")
        self.assertEqual(fields["minute"], "30")

        # R-09: the sweep must also run at the weekend so a late
        # Friday dissemination is not held until Monday.
        self.assertEqual(fields["day_of_week"], "*")
        self.assertEqual(kwargs, {"sweep": True})

    def test_nightly_sweep_can_be_disabled(self):
        self.config.nightly_sweep = False
        self.config.save()

        jobs = build_jobs_for_config(self.config)

        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0][1]["sweep"])

    def test_interval_below_floor_is_clamped(self):
        self.assertEqual(normalize_interval_minutes(1), 5)
        self.assertEqual(normalize_interval_minutes(0), 5)
        self.assertEqual(normalize_interval_minutes(-30), 5)

        self.config.interval_minutes = 1
        self.config.save()

        fields = cron_fields(
            build_interval_jobs(self.config)[0][0]
        )

        self.assertEqual(fields["minute"], "*/5")

    def test_interval_above_ceiling_is_clamped(self):
        self.assertEqual(normalize_interval_minutes(999), 60)

    def test_interval_is_snapped_to_a_divisor_of_sixty(self):
        # "*/25" fires at :00, :25, :50 then :00 - a ragged 10-minute
        # gap at every hour boundary.
        self.assertEqual(normalize_interval_minutes(25), 30)
        self.assertEqual(normalize_interval_minutes(20), 20)

    def test_invalid_interval_falls_back_to_default(self):
        self.assertEqual(normalize_interval_minutes(None), 20)
        self.assertEqual(normalize_interval_minutes("abc"), 20)

    def test_missing_active_window_polls_all_hours(self):
        self.config.active_window_start = None
        self.config.active_window_end = None
        self.config.save()

        fields = cron_fields(
            build_interval_jobs(self.config)[0][0]
        )

        self.assertEqual(fields["hour"], "*")

    def test_window_wrapping_midnight_falls_back_to_all_hours(self):
        # A wrapped range is not expressible as one cron field. Falling
        # back beats building a trigger that never fires.
        self.config.active_window_start = time(22, 0)
        self.config.active_window_end = time(4, 0)
        self.config.save()

        fields = cron_fields(
            build_interval_jobs(self.config)[0][0]
        )

        self.assertEqual(fields["hour"], "*")

    def test_inactive_interval_config_builds_no_jobs(self):
        self.config.is_active = False
        self.config.save()

        self.assertEqual(
            build_jobs_for_config(self.config),
            [],
        )

    @patch(
        "watcher.services.watcher_launcher."
        "reconcile_stale_running_runs"
    )
    @patch(
        "watcher.services.watcher_launcher."
        "subprocess.Popen"
    )
    def test_sweep_launch_appends_the_sweep_flag(
        self,
        mock_popen,
        mock_reconcile,
    ):
        mock_reconcile.return_value = False
        mock_popen.return_value = MagicMock(pid=4321)

        SubprocessWatcherLauncher.launch(sweep=True)

        cmd = mock_popen.call_args[0][0]

        self.assertIn("--sweep", cmd)
        self.assertIn("--auto-index", cmd)

    # ------------------------------------------------------------------
    # Regression cover
    # ------------------------------------------------------------------

    @patch(
        "watcher.services.watcher_launcher."
        "reconcile_stale_running_runs"
    )
    @patch(
        "watcher.services.watcher_launcher."
        "subprocess.Popen"
    )
    def test_regression_default_launch_command_is_unchanged(
        self,
        mock_popen,
        mock_reconcile,
    ):
        mock_reconcile.return_value = False
        mock_popen.return_value = MagicMock(pid=1234)

        SubprocessWatcherLauncher.launch()

        cmd = mock_popen.call_args[0][0]

        self.assertNotIn("--sweep", cmd)
        self.assertNotIn("--no-daily-chronicle", cmd)
        self.assertEqual(cmd[-3:], ["manage.py", "watcher", "--auto-index"])

    def test_regression_daily_still_builds_exactly_one_trigger(self):
        # The W-037 write-up proposed adding the sweep to every active
        # schedule. That would break this, and would double the run
        # volume of every existing daily config.
        daily = ScheduleConfig.objects.create(
            frequency="daily",
            is_active=True,
            start_date=date(2026, 9, 21),
            start_time=time(10, 0),
            daily_recur=1,
            run_times=["10:00"],
        )

        self.assertEqual(
            len(build_triggers_for_config(daily)),
            1,
        )
        self.assertEqual(
            len(build_jobs_for_config(daily)),
            1,
        )

    def test_regression_non_interval_jobs_carry_no_kwargs(self):
        for freq in ("onetime", "daily", "weekly", "monthly"):
            config = ScheduleConfig.objects.create(
                frequency=freq,
                is_active=True,
                start_date=date(2026, 9, 21),
                start_time=time(10, 0),
                daily_recur=1,
                weekly_days={"mon": True},
                run_times=["10:00"],
            )

            jobs = build_jobs_for_config(config)
            triggers = build_triggers_for_config(config)

            self.assertEqual(len(jobs), len(triggers), freq)

            for (trigger, kwargs), legacy in zip(jobs, triggers):
                self.assertEqual(kwargs, {}, freq)
                self.assertEqual(str(trigger), str(legacy), freq)

    def test_regression_new_fields_default_safely_on_existing_rows(self):
        legacy = ScheduleConfig.objects.create(
            frequency="daily",
            is_active=True,
            run_times=["10:00"],
        )
        legacy.refresh_from_db()

        self.assertEqual(legacy.interval_minutes, 20)
        self.assertIsNone(legacy.active_window_start)
        self.assertIsNone(legacy.active_window_end)
        self.assertTrue(legacy.nightly_sweep)