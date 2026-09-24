"""
Daily read-only copy of the PostgreSQL database into a standalone SQLite file.

Usage:
    python manage.py backup_to_sqlite
    python manage.py backup_to_sqlite --keep 14 --out-dir D:\\Backups\\watcher

Safety properties:
- Reads PostgreSQL only (SELECT inside one REPEATABLE READ transaction), so the
  running watcher, scheduler and API are never blocked or modified.
- The SQLite connection is added at runtime only; settings.DATABASES is not
  changed, so the app and the test suite are unaffected.
- Writes to a temporary file and renames it only after a successful copy and
  row-count check, so a failed run never leaves a half-written backup.
- Keeps the newest N backups (default 7) and deletes older ones.
"""

import time
from datetime import datetime
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

BACKUP_ALIAS = "sqlite_backup"
FILE_PREFIX = "sec_agent_backup_"
BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Copy the PostgreSQL database into a dated SQLite backup file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out-dir",
            default=str(Path(settings.BASE_DIR) / "backups"),
            help="Folder for backup files (default: <backend>/backups).",
        )
        parser.add_argument(
            "--keep",
            type=int,
            default=7,
            help="How many most recent backups to keep (default: 7).",
        )

    def handle(self, *args, **options):
        out_dir = Path(options["out_dir"])
        keep = options["keep"]

        if keep < 1:
            raise CommandError("--keep must be at least 1.")

        out_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_path = out_dir / f"{FILE_PREFIX}{stamp}.sqlite3"
        temp_path = out_dir / f"{FILE_PREFIX}{stamp}.sqlite3.tmp"

        started = time.monotonic()

        self._register_sqlite(temp_path)

        try:
            self._create_schema()
            counts = self._copy_all_models()
            self._verify(counts)
        except Exception as exc:
            self._close_sqlite()
            temp_path.unlink(missing_ok=True)
            raise CommandError(f"Backup failed: {exc}") from exc

        self._close_sqlite()
        temp_path.replace(final_path)

        removed = self._apply_retention(out_dir, keep)

        total_rows = sum(counts.values())
        size_mb = final_path.stat().st_size / (1024 * 1024)

        self.stdout.write(self.style.SUCCESS(
            f"Backup complete: {final_path} "
            f"({total_rows} rows, {len(counts)} tables, "
            f"{size_mb:.1f} MB, {time.monotonic() - started:.1f}s)"
        ))

        if removed:
            self.stdout.write(f"Removed {removed} old backup(s).")

    # ------------------------------------------------------------------ setup

    def _register_sqlite(self, path):
        connections.databases[BACKUP_ALIAS] = {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(path),
            "ATOMIC_REQUESTS": False,
            "AUTOCOMMIT": True,
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
            "OPTIONS": {},
            "TIME_ZONE": None,
            "USER": "",
            "PASSWORD": "",
            "HOST": "",
            "PORT": "",
            "TEST": {},
        }

    def _close_sqlite(self):
        if BACKUP_ALIAS in connections:
            connections[BACKUP_ALIAS].close()
        connections.databases.pop(BACKUP_ALIAS, None)

    def _create_schema(self):
        # Build tables straight from the current models instead of running
        # migrations: some third-party migrations (django_apscheduler) query
        # the default database from RunPython and fail on a second alias.
        # create_model() also creates each model's many-to-many tables.
        with connections[BACKUP_ALIAS].schema_editor() as editor:
            for model in apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)

    # ------------------------------------------------------------------- copy

    def _models_to_copy(self):
        return [
            model
            for model in apps.get_models(include_auto_created=True)
            if model._meta.managed and not model._meta.proxy
        ]

    def _copy_all_models(self):
        counts = {}
        target = connections[BACKUP_ALIAS]

        # One consistent snapshot of PostgreSQL; read-only, never blocks writers.
        with transaction.atomic(using="default"):
            with connections["default"].cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )

            with target.constraint_checks_disabled():
                with transaction.atomic(using=BACKUP_ALIAS):
                    for model in self._models_to_copy():
                        counts[model._meta.db_table] = self._copy_model(model)

        return counts

    def _copy_model(self, model):
        source = (
            model._base_manager
            .using("default")
            .order_by("pk")
            .iterator(chunk_size=BATCH_SIZE)
        )

        batch, copied = [], 0

        for obj in source:
            batch.append(obj)
            if len(batch) >= BATCH_SIZE:
                copied += self._write_batch(model, batch)
                batch = []

        if batch:
            copied += self._write_batch(model, batch)

        return copied

    def _write_batch(self, model, batch):
        for obj in batch:
            obj._state.db = BACKUP_ALIAS
            obj._state.adding = True
        model._base_manager.using(BACKUP_ALIAS).bulk_create(
            batch, batch_size=BATCH_SIZE
        )
        return len(batch)

    # ----------------------------------------------------------------- checks

    def _verify(self, counts):
        mismatches = []

        for model in self._models_to_copy():
            table = model._meta.db_table
            actual = model._base_manager.using(BACKUP_ALIAS).count()
            if actual != counts.get(table, 0):
                mismatches.append(f"{table}: expected {counts[table]}, got {actual}")

        if mismatches:
            raise CommandError("Row count mismatch: " + "; ".join(mismatches))

        connections[BACKUP_ALIAS].check_constraints()

    # -------------------------------------------------------------- retention

    def _apply_retention(self, out_dir, keep):
        backups = sorted(
            out_dir.glob(f"{FILE_PREFIX}*.sqlite3"),
            key=lambda p: p.name,
            reverse=True,
        )
        removed = 0
        for old in backups[keep:]:
            old.unlink(missing_ok=True)
            removed += 1
        return removed