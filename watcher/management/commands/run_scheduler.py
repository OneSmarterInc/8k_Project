import logging
from django.conf import settings
from apscheduler.schedulers.blocking import BlockingScheduler
from django.core.management.base import BaseCommand
from django_apscheduler.jobstores import DjangoJobStore
from watcher.models import ScheduleConfig
from watcher.services.schedule_manager import build_triggers_for_config
from watcher.api.views import _run_watcher

logger = logging.getLogger(__name__)

def my_job():
    """
    The actual task that runs when a schedule hits.
    """
    print("Scheduler hit! Triggering watcher script...")
    _run_watcher(daily_chronicle=True)

class Command(BaseCommand):
    help = "Runs APScheduler to execute tasks based on the ScheduleConfig database."

    def handle(self, *args, **options):
        scheduler = BlockingScheduler(timezone=settings.TIME_ZONE)
        scheduler.add_jobstore(DjangoJobStore(), "default")

        # Clear existing jobs to reload them from the config
        scheduler.remove_all_jobs()

        config = ScheduleConfig.objects.first()
        if config and config.is_active:
            triggers = build_triggers_for_config(config)
            
            for i, trigger in enumerate(triggers):
                scheduler.add_job(
                    my_job,
                    trigger=trigger,
                    id=f"watcher_job_{i}",
                    max_instances=1,
                    replace_existing=True,
                )
            
            self.stdout.write(self.style.SUCCESS(f"Loaded {len(triggers)} triggers from ScheduleConfig."))
        else:
            self.stdout.write(self.style.WARNING("ScheduleConfig is inactive or empty. No jobs loaded."))

        try:
            self.stdout.write(self.style.SUCCESS("Starting scheduler..."))
            scheduler.start()
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS("Stopping scheduler..."))
            scheduler.shutdown()
            self.stdout.write(self.style.SUCCESS("Scheduler shut down successfully!"))
