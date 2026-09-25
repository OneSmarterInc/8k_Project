"""
W-039 (operational half): make `migrate` create the cache table.

The login throttle counts attempts in the Django cache. With
CACHE_BACKEND=database that cache is a real table, but
`createcachetable` is a separate command that is easy to forget on a
fresh deployment — and forgetting it means the first login fails with
"relation does not exist".

This migration closes that gap: `python manage.py migrate` now creates
the table as part of the normal deploy, with no extra step.

Safety properties:

- No-op unless the default cache is actually DatabaseCache. Nothing is
  created for locmem or redis deployments.
- `createcachetable` is itself idempotent: it checks for the table
  first, so running this on a system where the table already exists
  changes nothing.
- Reversing this migration does NOT drop the table. Dropping a cache
  table on a rollback would take logins down for a problem the
  rollback was not trying to solve.
- Creates no models and alters no schema state, so it cannot conflict
  with any future model migration.
"""

from django.conf import settings
from django.core.management import call_command
from django.db import migrations

DB_CACHE_BACKEND = "django.core.cache.backends.db.DatabaseCache"


def create_cache_table(apps, schema_editor):
    caches = getattr(settings, "CACHES", {}) or {}
    default = caches.get("default") or {}

    if default.get("BACKEND") != DB_CACHE_BACKEND:
        # locmem / redis / dummy: nothing to create.
        return

    call_command(
        "createcachetable",
        database=schema_editor.connection.alias,
        verbosity=0,
    )


def noop_reverse(apps, schema_editor):
    """
    Deliberately does nothing.

    Dropping the cache table on a rollback would break every login for
    a reason unrelated to whatever is being rolled back.
    """
    return


class Migration(migrations.Migration):

    dependencies = [
        ("watcher", "0024_scheduleconfig_interval_polling"),
    ]

    operations = [
        migrations.RunPython(
            create_cache_table,
            noop_reverse,
        ),
    ]
