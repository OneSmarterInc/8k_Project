from django.apps import AppConfig


class WatcherConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "watcher"

    def ready(self):
        # W-039: register the cache-configuration system check.
        # Import only; registration happens via the @register()
        # decorator, and the module has no other side effects.
        from watcher import checks  # noqa: F401