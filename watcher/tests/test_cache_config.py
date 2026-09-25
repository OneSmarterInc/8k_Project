"""
W-039: the cache backend must be shared across processes in production.

These tests cover the system check only. They never touch the real cache
configuration, so they cannot affect other tests.
"""

from django.conf import settings
from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings

from watcher.checks import (
    CACHE_NOT_SHARED,
    check_cache_is_shared,
)

LOCMEM = "django.core.cache.backends.locmem.LocMemCache"
DUMMY = "django.core.cache.backends.dummy.DummyCache"
REDIS = "django.core.cache.backends.redis.RedisCache"
DBCACHE = "django.core.cache.backends.db.DatabaseCache"

PER_PROCESS = (LOCMEM, DUMMY)


class CacheCheckTests(SimpleTestCase):

    def run_check(self):
        return check_cache_is_shared(app_configs=None)

    # ------------------------------------------------------------------
    # Silent where it should be
    # ------------------------------------------------------------------

    @override_settings(
        DEBUG=True,
        CACHES={"default": {"BACKEND": LOCMEM}},
        PER_PROCESS_CACHE_BACKENDS=PER_PROCESS,
    )
    def test_debug_mode_is_silent(self):
        # A single runserver process makes LocMemCache fine.
        self.assertEqual(self.run_check(), [])

    @override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": REDIS, "LOCATION": "redis://x/1"}},
        PER_PROCESS_CACHE_BACKENDS=PER_PROCESS,
    )
    def test_redis_backend_is_accepted(self):
        self.assertEqual(self.run_check(), [])

    @override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": DBCACHE, "LOCATION": "t"}},
        PER_PROCESS_CACHE_BACKENDS=PER_PROCESS,
    )
    def test_database_backend_is_accepted(self):
        self.assertEqual(self.run_check(), [])

    # ------------------------------------------------------------------
    # Warns where it should
    # ------------------------------------------------------------------

    @override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": LOCMEM}},
        PER_PROCESS_CACHE_BACKENDS=PER_PROCESS,
    )
    def test_locmem_in_production_warns(self):
        warnings = self.run_check()

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].id, CACHE_NOT_SHARED)
        self.assertIn("throttle", warnings[0].msg)

    @override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": DUMMY}},
        PER_PROCESS_CACHE_BACKENDS=PER_PROCESS,
    )
    def test_dummy_cache_in_production_warns(self):
        self.assertEqual(len(self.run_check()), 1)

    @override_settings(
        DEBUG=False,
        CACHES={},
        PER_PROCESS_CACHE_BACKENDS=PER_PROCESS,
    )
    def test_missing_caches_block_warns(self):
        # An empty BACKEND string must not crash the check.
        warnings = self.run_check()

        self.assertEqual(len(warnings), 1)
        self.assertIn("unset", warnings[0].hint)


class DefaultCacheUsableTests(TestCase):
    """
    Must be a TestCase, not a SimpleTestCase.

    With CACHE_BACKEND=database the cache IS the database, so a
    SimpleTestCase raises DatabaseOperationForbidden the moment
    cache.set() runs. TestCase works for every backend.
    """

    def test_default_cache_is_usable(self):
        # The schedule view stores "abort_automation_run" here and the
        # login throttle stores its counters, so an unreachable cache
        # breaks pausing automation and every login.
        cache = caches["default"]
        backend = settings.CACHES["default"]["BACKEND"]

        try:
            cache.set("w039-probe", "value", timeout=30)
            self.assertEqual(cache.get("w039-probe"), "value")

            cache.delete("w039-probe")
            self.assertIsNone(cache.get("w039-probe"))

        except Exception as exc:
            self.fail(
                "The configured cache backend is unreachable, so the "
                "login throttle and the automation pause flag would "
                "both fail at runtime.\n"
                f"  backend: {backend}\n"
                f"  error:   {type(exc).__name__}: {exc}\n"
                "Start the cache service, or set "
                "CACHE_BACKEND=database and run "
                "'python manage.py createcachetable'."
            )

class AuthCookieSecurityCheckTests(SimpleTestCase):
    """
    P-01 / P-05: watcher.E001 and watcher.E002.

    These run only under `manage.py check --deploy`, so the assertions
    call the function directly rather than relying on registration.
    """

    def run_check(self):
        from watcher.checks import check_auth_cookie_security

        return check_auth_cookie_security(app_configs=None)

    def ids(self):
        return {problem.id for problem in self.run_check()}

    # ------------------------------------------------------------------
    # Silent where it should be
    # ------------------------------------------------------------------

    @override_settings(
        DEBUG=True,
        AUTH_COOKIE_SECURE=False,
        AUTH_COOKIE_SAMESITE="Lax",
    )
    def test_localhost_development_is_silent(self):
        # AUTH_COOKIE_SECURE=False is CORRECT on http://localhost: a
        # Secure cookie is never sent back, which 401s every request
        # after login. Warning about it here would be noise.
        self.assertEqual(self.run_check(), [])

    @override_settings(
        DEBUG=False,
        AUTH_COOKIE_SECURE=True,
        AUTH_COOKIE_SAMESITE="Lax",
    )
    def test_correct_production_config_is_silent(self):
        self.assertEqual(self.run_check(), [])

    # ------------------------------------------------------------------
    # Loud where it should be
    # ------------------------------------------------------------------

    @override_settings(
        DEBUG=False,
        AUTH_COOKIE_SECURE=False,
        AUTH_COOKIE_SAMESITE="Lax",
    )
    def test_insecure_cookie_in_production_is_an_error(self):
        problems = self.run_check()

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].id, "watcher.E001")
        self.assertIn("clear text", problems[0].msg)

    @override_settings(
        DEBUG=False,
        AUTH_COOKIE_SECURE=True,
        AUTH_COOKIE_SAMESITE="None",
    )
    def test_samesite_none_is_an_error(self):
        problems = self.run_check()

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].id, "watcher.E002")
        self.assertIn("CSRF", problems[0].msg)

    @override_settings(
        DEBUG=False,
        AUTH_COOKIE_SECURE=True,
        AUTH_COOKIE_SAMESITE="none",
    )
    def test_samesite_none_is_matched_case_insensitively(self):
        self.assertIn("watcher.E002", self.ids())

    @override_settings(
        DEBUG=False,
        AUTH_COOKIE_SECURE=False,
        AUTH_COOKIE_SAMESITE="None",
    )
    def test_both_errors_can_fire_together(self):
        self.assertEqual(self.ids(), {"watcher.E001", "watcher.E002"})


class TlsSettingsDefaultTests(SimpleTestCase):
    """
    P-01: every TLS setting must default OFF so localhost keeps working.

    SECURE_SSL_REDIRECT in particular would make the dev server bounce
    every request to https:// and nothing would load.
    """

    def test_tls_settings_default_off(self):
        from django.conf import settings as s

        self.assertFalse(s.SECURE_SSL_REDIRECT)
        self.assertFalse(s.SESSION_COOKIE_SECURE)
        self.assertFalse(s.CSRF_COOKIE_SECURE)
        self.assertEqual(s.SECURE_HSTS_SECONDS, 0)

    def test_hsts_subsettings_are_off_when_hsts_is_off(self):
        # Sending HSTS headers from a host not fully on https locks
        # browsers out of it for the duration.
        from django.conf import settings as s

        self.assertFalse(s.SECURE_HSTS_INCLUDE_SUBDOMAINS)
        self.assertFalse(s.SECURE_HSTS_PRELOAD)

    def test_proxy_ssl_header_is_not_trusted_by_default(self):
        from django.conf import settings as s

        self.assertIsNone(s.SECURE_PROXY_SSL_HEADER)