import os
import sys
import subprocess
import logging
from watcher.services.watcher_lock import (
    reconcile_stale_running_runs,
)
from django.conf import settings
from watcher.models import AutomationRun


logger = logging.getLogger(__name__)


class SubprocessWatcherLauncher:
    """
    Production-safe launcher for the SEC Watcher.

    The watcher subprocess is detached from the Django process and writes
    directly to watcher_latest.log.

    This is important because the watcher must continue running even if the
    Django/API/scheduler process that launched it is restarted or terminated.
    """

    @classmethod
    def is_running(cls):
        """
        Return True only when the watcher PostgreSQL advisory lock confirms
        that a watcher process is actually running.

        Any stale RUNNING AutomationRun rows left by a crashed process are
        reconciled automatically.
        """
        return reconcile_stale_running_runs()
    
    @classmethod
    def launch(
        cls,
        force=False,
        daily_chronicle=True,
    ):
        """
        Launch the watcher as an independent subprocess.

        Existing behaviour is preserved:
        - Uses the same Python interpreter.
        - Runs manage.py watcher --auto-index.
        - Daily Chronicle remains enabled by default.
        - Adds --no-daily-chronicle only when explicitly disabled.
        - Uses watcher_latest.log.
        - Prevents duplicate launches unless force=True.
        - Returns True when a launch is attempted.
        - Returns False when another watcher is already running.
        """

        if not force and cls.is_running():
            logger.warning(
                "Watcher launch skipped: "
                "AutomationRun is currently RUNNING."
            )
            return False

        logger.info(
            "Spawning watcher subprocess..."
        )

        base_dir = str(
            settings.BASE_DIR
        )

        log_path = os.path.join(
            base_dir,
            "watcher_latest.log",
        )

        cmd = [
            sys.executable,
            "-u",
            "manage.py",
            "watcher",
            "--auto-index",
        ]

        # W-029:
        # The watcher command already supports this flag.
        # Only append it when Daily Chronicle is explicitly disabled.
        if not daily_chronicle:
            cmd.append(
                "--no-daily-chronicle"
            )

        # Each watcher launch starts a fresh log file.
        with open(
            log_path,
            "wb",
        ) as log_file:
            log_file.write(
                b"Starting watcher via "
                b"SubprocessWatcherLauncher...\n"
            )

        popen_kwargs = {
            "cwd": base_dir,

            # Child output goes directly to the log file.
            "stderr": subprocess.STDOUT,

            # Watcher is non-interactive.
            "stdin": subprocess.DEVNULL,

            "close_fds": True,
        }

        # Preserve W-023 detached subprocess behaviour.
        if os.name == "nt":
            popen_kwargs[
                "creationflags"
            ] = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            popen_kwargs[
                "start_new_session"
            ] = True

        with open(
            log_path,
            "ab",
            buffering=0,
        ) as log_file:

            popen_kwargs[
                "stdout"
            ] = log_file

            process = subprocess.Popen(
                cmd,
                **popen_kwargs,
            )

        logger.info(
            "Watcher subprocess started "
            "successfully with PID %s.",
            process.pid,
        )

        return True