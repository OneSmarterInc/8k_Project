import time
import logging
from django.conf import settings
from apscheduler.schedulers.background import BackgroundScheduler
from django.core.management.base import BaseCommand
from django_apscheduler.jobstores import DjangoJobStore
from django.db import connection
from django.forms.models import model_to_dict
from watcher.models import ScheduleConfig
from watcher.services.schedule_manager import (
    build_jobs_for_config,
    build_triggers_for_config,
)
from watcher.services.watcher_launcher import SubprocessWatcherLauncher

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Configure root logger to output APScheduler logs explicitly
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def my_job(sweep=False):
    """
    The actual task that runs when a schedule hits.
    Spawns the SEC watcher asynchronously so APScheduler threads are never blocked.

    W-037: `sweep` defaults to False so jobs already persisted in the
    DjangoJobStore with no kwargs keep loading and calling correctly.
    """
    logger.info(
        "Scheduler triggered (sweep=%s)! Invoking WatcherLauncher...",
        sweep,
    )
    SubprocessWatcherLauncher.launch(sweep=sweep)


class Command(BaseCommand):
    help = "Runs a production-ready APScheduler daemon to execute SEC watcher tasks."

    def _config_changed(self, old_config, new_config):
        """
        Returns True if the effective scheduling configuration changed.
        """
        if bool(old_config) != bool(new_config):
            return True
        if not old_config and not new_config:
            return False
            
        # Compare dictionaries
        return model_to_dict(old_config) != model_to_dict(new_config)

    def handle(self, *args, **options):
        tz = ZoneInfo(
            settings.TIME_ZONE
        )

        scheduler = BackgroundScheduler(
            timezone=tz
        )
        scheduler.add_jobstore(DjangoJobStore(), "default")
        
        self.stdout.write(self.style.SUCCESS("Starting BackgroundScheduler daemon..."))
        scheduler.start()

        def load_jobs():
            """
            Safely drops all scheduler-owned watcher jobs and recreates them from config.
            """
            # Remove ONLY our specific watcher jobs
            for job in scheduler.get_jobs():
                if str(job.id).startswith("watcher_job_"):
                    job.remove()
                    
            config = ScheduleConfig.objects.first()
            if config and config.is_active:
                # W-037: build_jobs_for_config returns (trigger, kwargs)
                # pairs. For every pre-existing frequency the kwargs are
                # empty and the trigger list is unchanged.
                jobs = build_jobs_for_config(config)

                is_interval = (
                    str(config.frequency or "").lower() == "interval"
                )

                for i, (trigger, job_kwargs) in enumerate(jobs):
                    is_sweep = bool(job_kwargs.get("sweep"))

                    # A 1-hour grace is right for a once-a-day schedule
                    # and for the nightly sweep. It is wrong for a
                    # 20-minute poll, where a run delayed 55 minutes
                    # should simply be dropped in favour of the next
                    # scheduled poll. Keyed off the frequency, NOT off
                    # the sweep flag, so daily/weekly/monthly keep the
                    # original 3600.
                    if is_interval and not is_sweep:
                        grace = 1200
                    else:
                        grace = 3600

                    scheduler.add_job(
                        my_job,
                        trigger=trigger,
                        kwargs=job_kwargs or None,
                        # Prefix preserved: load_jobs only removes ids
                        # starting with "watcher_job_", so the sweep job
                        # is cleaned up when automation is switched off
                        # instead of orphaning in the jobstore.
                        id=f"watcher_job_{i}",
                        max_instances=1,
                        coalesce=True,
                        misfire_grace_time=grace,
                        replace_existing=True,
                    )
                self.stdout.write(self.style.SUCCESS(f"Loaded {len(jobs)} scheduled triggers."))
            else:
                self.stdout.write(self.style.WARNING("Automation disabled or config empty. Watcher jobs removed."))
            return config
            
        # Initial load
        current_config = None
        try:
            current_config = load_jobs()
        except Exception as e:
            logger.error(f"Failed initial job load: {e}")

        # Main polling loop (Hot-Reloading)
        try:
            while True:
                time.sleep(15) # Poll every 15 seconds
                
                try:
                    # Clean up old DB connections to prevent staleness/leaks in long-running daemon
                    connection.close_if_unusable_or_obsolete()
                    
                    new_config = ScheduleConfig.objects.first()
                    if self._config_changed(current_config, new_config):
                        self.stdout.write(self.style.WARNING("\n[HOT-RELOAD] ScheduleConfig change detected. Reloading jobs..."))
                        current_config = load_jobs()
                        
                except Exception as db_err:
                    # DB might be temporarily unavailable; do not crash daemon
                    logger.error(f"Database error during polling: {db_err}")
                    
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS("\nGracefully shutting down scheduler..."))
            scheduler.shutdown()
            self.stdout.write(self.style.SUCCESS("Scheduler shut down successfully."))