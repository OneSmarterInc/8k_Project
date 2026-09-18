from django.apps import AppConfig


class WatcherConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'watcher'

    def ready(self):
        import sys
        # Only run this cleanup if we are starting the dev server
        if 'runserver' in sys.argv:
            try:
                from watcher.models import AutomationRun
                stuck_runs = AutomationRun.objects.filter(status=AutomationRun.Status.RUNNING)
                if stuck_runs.exists():
                    stuck_runs.update(status=AutomationRun.Status.FAILED)
                    print(f"--- [CLEANUP] Reset {stuck_runs.count()} stuck automation runs to 'failed' ---")
            except Exception:
                # Catch exceptions in case database isn't ready or models aren't fully loaded
                pass
