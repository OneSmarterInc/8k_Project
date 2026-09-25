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
from django.core.checks import Error, Warning, register

CACHE_NOT_SHARED = "watcher.W001"
INSECURE_AUTH_COOKIE = "watcher.E001"
UNSAFE_SAMESITE = "watcher.E002"

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

@register(deploy=True)
def check_auth_cookie_security(app_configs, **kwargs):
    """
    P-01 / P-05: refuse to let an insecure cookie configuration ship.

    Registered with deploy=True, so it runs ONLY under
    `manage.py check --deploy`. Ordinary `manage.py check`, runserver,
    and the test suite never see it - a laptop on http://localhost is
    supposed to have AUTH_COOKIE_SECURE=False, and warning about that
    every time would train everyone to ignore the output.

    The acceptance test for P-01 is that `check --deploy` comes back
    clean on the server before go-live.
    """
    problems = []

    if not settings.DEBUG and not getattr(
        settings,
        "AUTH_COOKIE_SECURE",
        False,
    ):
        problems.append(
            Error(
                "AUTH_COOKIE_SECURE is False with DEBUG off. The "
                "session cookie will travel in clear text.",
                hint=(
                    "Set AUTH_COOKIE_SECURE=True in the deployment "
                    ".env. Leave it unset on a laptop: a Secure cookie "
                    "is never sent back over http://localhost, which "
                    "401s every request after login."
                ),
                id=INSECURE_AUTH_COOKIE,
            )
        )

    samesite = getattr(settings, "AUTH_COOKIE_SAMESITE", "Lax")

    csrf_enforced = getattr(
        settings,
        "CSRF_ENFORCE_COOKIE_AUTH",
        False,
    )

    if str(samesite).lower() == "none" and not csrf_enforced:
        # SameSite=None is legitimate - a Vercel frontend calling an
        # AWS backend needs it. What is not legitimate is setting it
        # while CSRF enforcement is off, because SameSite=Lax was the
        # only thing protecting cookie-authenticated POSTs.
        problems.append(
            Error(
                "AUTH_COOKIE_SAMESITE is None while "
                "CSRF_ENFORCE_COOKIE_AUTH is off. That removes the "
                "only CSRF protection on cookie-authenticated "
                "requests.",
                hint=(
                    "Either keep SameSite=Lax (correct when the "
                    "frontend and API share a host, including behind a "
                    "Vercel rewrite), or set "
                    "CSRF_ENFORCE_COOKIE_AUTH=True so unsafe methods "
                    "are CSRF-checked. A cross-origin deployment also "
                    "needs AUTH_COOKIE_SECURE=True, "
                    "CORS_ALLOW_CREDENTIALS=True and the frontend "
                    "origin in CORS_ALLOWED_ORIGINS."
                ),
                id=UNSAFE_SAMESITE,
            )
        )

    return problems