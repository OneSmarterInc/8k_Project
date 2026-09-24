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