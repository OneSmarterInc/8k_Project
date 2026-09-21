import os
import sys
import subprocess
import logging
import threading
from django.conf import settings
from watcher.models import AutomationRun

logger = logging.getLogger(__name__)

class SubprocessWatcherLauncher:
    """
    Production-safe launcher for the SEC Watcher.
    Spawns the watcher as a detached subprocess and redirects output.
    """
    
    @classmethod
    def is_running(cls):
        """
        Returns True if a watcher is currently marked as running in the database.
        (This is a fast check for UI/Scheduler before launching).
        Strict overlap protection is handled by PostgreSQL advisory locks inside watcher.py.
        """
        return AutomationRun.objects.filter(status=AutomationRun.Status.RUNNING).exists()

    @classmethod
    def launch(cls, force=False):
        """
        Launches the watcher subprocess.
        If force=False and it looks like it's running, it will skip launching.
        Returns True if a launch was attempted.
        """
        if not force and cls.is_running():
            logger.warning("Watcher launch skipped: AutomationRun is currently RUNNING.")
            return False
            
        logger.info("Spawning watcher subprocess...")
        
        log_path = os.path.join(settings.BASE_DIR, "watcher_latest.log")
        
        # Use unbuffered python (-u) so logs appear in real-time
        cmd = [sys.executable, "-u", "manage.py", "watcher", "--auto-index"]
        
        # Open the log file in write mode to overwrite previous runs
        with open(log_path, "wb") as f:
            f.write(b"Starting watcher via SubprocessWatcherLauncher...\n")
        
        # Open in append mode for the subprocess (binary mode for reading raw bytes from pipe)
        log_file = open(log_path, "ab")
        
        # Spawn detached process with piped stdout
        process = subprocess.Popen(
            cmd,
            cwd=str(settings.BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True
        )
        
        # Create a lightweight daemon thread to stream output to both terminal and log file
        def stream_output(pipe, file):
            for line in iter(pipe.readline, b''):
                # Print to terminal
                sys.stdout.write(line.decode("utf-8", errors="replace"))
                sys.stdout.flush()
                # Write to log file
                file.write(line)
                file.flush()
            pipe.close()
            file.close()
            sys.stdout.write("\n--- Watcher process finished ---\n")
            sys.stdout.flush()

        thread = threading.Thread(target=stream_output, args=(process.stdout, log_file))
        thread.daemon = True
        thread.start()
        
        return True
