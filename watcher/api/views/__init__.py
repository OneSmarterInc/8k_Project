"""
watcher.api.views

This used to be one 777-line module. It is now a package split by
concern, with every public name re-exported here so that no import
anywhere else in the codebase had to change:

    runs.py      - run history, manual trigger, log tailing
    filings.py   - filing list, filters, pagination, Review Queue
    smtp.py      - SMTP configuration (W-034)
    schedule.py  - schedule configuration (W-037)

`from watcher.api.views import filings` resolves exactly as before.
"""

from .runs import (
    run_logs,
    runs,
    trigger_run,
)
from .filings import (
    FilingPagination,
    filings,
)
from .queue_exports import (
    queue_export_download,
    queue_exports,
)
from .smtp import SMTPConfigView
from .schedule import ScheduleConfigView

# Re-exported for backward compatibility only. Existing code patches
# "watcher.api.views.SubprocessWatcherLauncher.launch" (see
# tests/test_api_auth.py). Because this binds the SAME class object that
# runs.py imported, patching .launch through this name still reaches the
# view. Keeping it here means the split required no change to any caller
# or test.
from watcher.services.watcher_launcher import (  # noqa: F401
    SubprocessWatcherLauncher,
)

__all__ = [
    "runs",
    "trigger_run",
    "run_logs",
    "FilingPagination",
    "filings",
    "queue_exports",
    "queue_export_download",
    "SMTPConfigView",
    "ScheduleConfigView",
    "SubprocessWatcherLauncher",
]