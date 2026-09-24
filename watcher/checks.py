"""
W-039: startup checks for the cache configuration.

The login throttle (watcher/api/throttles.py) counts attempts in the
Django cache. A per-process backend means each gunicorn worker keeps its
own counter, so the real limit is the configured rate times the worker
count. Nothing in the code used to hint at that.

This check makes it loud. It only warns; it never blocks startup, so a
misconfiguration cannot take the service down.

Run it explicitly with:

    python manage.py check
"""

from django.conf import settings
from django.core.checks import Warning, register

CACHE_NOT_SHARED = "watcher.W001"

DEFAULT_PER_PROCESS_BACKENDS = (
    "django.core.cache.backends.locmem.LocMemCache",
    "django.core.cache.backends.dummy.DummyCache",
)


@register()
def check_cache_is_shared(app_configs, **kwargs):
    """
    Warn when DEBUG is off and the cache backend is per-process.

    Silent in development, where a single runserver process makes
    LocMemCache perfectly adequate.
    """

    if getattr(settings, "DEBUG", False):
        return []

    caches = getattr(settings, "CACHES", {}) or {}
    default = caches.get("default") or {}
    backend = str(default.get("BACKEND", ""))

    per_process = tuple(
        getattr(
            settings,
            "PER_PROCESS_CACHE_BACKENDS",
            DEFAULT_PER_PROCESS_BACKENDS,
        )
    )

    # An empty BACKEND means no CACHES block at all, and Django's own
    # fallback in that case is LocMemCache - the exact situation this
    # check exists for. Treat it as per-process.
    if backend and backend not in per_process:
        return []

    return [
        Warning(
            "The default cache backend is per-process, so the login "
            "throttle counts attempts separately in every worker.",
            hint=(
                "With N workers the effective login limit becomes the "
                "configured rate times N. Set CACHE_BACKEND=redis with "
                "REDIS_URL (needs the redis package), or "
                "CACHE_BACKEND=database followed by "
                "'python manage.py createcachetable'. "
                f"Current backend: {backend or 'unset'}."
            ),
            id=CACHE_NOT_SHARED,
        )
    ]