import os
import sys
import subprocess
import logging

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
        Returns True if a watcher is currently marked as running in the database.

        This remains the existing fast UI/scheduler check.
        Strict overlap protection continues to be handled by the PostgreSQL
        advisory lock inside watcher.py.
        """
        return AutomationRun.objects.filter(
            status=AutomationRun.Status.RUNNING
        ).exists()

    @classmethod
    def launch(cls, force=False):
        """
        Launch the watcher as an independent subprocess.

        Existing behaviour is preserved:
        - Uses the same Python interpreter.
        - Runs manage.py watcher --auto-index.
        - Uses watcher_latest.log.
        - Prevents duplicate launches unless force=True.
        - Returns True when a launch is attempted.
        - Returns False when an existing RUNNING row prevents launch.

        The only lifecycle change is that the child no longer depends on a
        PIPE-reading thread owned by Django.
        """
        if not force and cls.is_running():
            logger.warning(
                "Watcher launch skipped: AutomationRun is currently RUNNING."
            )
            return False

        logger.info("Spawning watcher subprocess...")

        base_dir = str(settings.BASE_DIR)
        log_path = os.path.join(base_dir, "watcher_latest.log")

        # Keep the existing watcher command unchanged.
        cmd = [
            sys.executable,
            "-u",
            "manage.py",
            "watcher",
            "--auto-index",
        ]

        # Preserve existing behaviour: each new watcher launch starts a fresh
        # watcher_latest.log file.
        with open(log_path, "wb") as log_file:
            log_file.write(
                b"Starting watcher via SubprocessWatcherLauncher...\n"
            )

        popen_kwargs = {
            "cwd": base_dir,

            # IMPORTANT:
            # Send output directly to the log file rather than PIPE.
            # The child therefore does not depend on Django draining stdout.
            "stderr": subprocess.STDOUT,

            # The watcher is non-interactive.
            "stdin": subprocess.DEVNULL,

            "close_fds": True,
        }

        # Detach from the parent process/session.
        if os.name == "nt":
            # Windows:
            # do not inherit Django's console/process group.
            popen_kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            # Linux/macOS:
            # create a new session so the watcher survives the launching
            # Django process terminating.
            popen_kwargs["start_new_session"] = True

        # Popen duplicates/inherits the redirected stdout handle for the child.
        # The parent can safely close its own file handle immediately afterward.
        with open(log_path, "ab", buffering=0) as log_file:
            popen_kwargs["stdout"] = log_file

            process = subprocess.Popen(
                cmd,
                **popen_kwargs,
            )

        logger.info(
            "Watcher subprocess started successfully with PID %s.",
            process.pid,
        )

        return True