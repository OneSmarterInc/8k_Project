"""Run history, manual triggering, and log tailing.

Split out of the former single-file watcher/api/views.py. Behaviour is
unchanged; watcher.api.views still re-exports every name, so existing
imports such as `from watcher.api.views import runs` keep working.
"""

import os

from django.conf import settings

from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from watcher.models import AutomationRun
from watcher.services.watcher_launcher import (
    SubprocessWatcherLauncher,
)

from ..serializers import AutomationRunSerializer


@api_view(["GET"])
def runs(request):
    queryset = (
        AutomationRun.objects
        .all()
        .order_by("-started_at")
    )

    serializer = AutomationRunSerializer(
        queryset,
        many=True,
    )

    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_run(request):
    """
    Start one watcher run.

    W-029:
    Propagate the Daily Chronicle choice from the API
    to SubprocessWatcherLauncher.

    Existing clients remain compatible because
    Daily Chronicle defaults to True.
    """

    daily_chronicle = (
        request.data.get(
            "daily_chronicle",
            True,
        )
    )

    # Do not use bool(value) here because:
    #
    # bool("false") == True
    #
    # Require a genuine JSON boolean.
    if not isinstance(
        daily_chronicle,
        bool,
    ):
        return Response(
            {
                "status": "error",
                "message": (
                    "daily_chronicle must be "
                    "true or false."
                ),
            },
            status=400,
        )

    launched = (
        SubprocessWatcherLauncher.launch(
            daily_chronicle=(
                daily_chronicle
            ),
        )
    )

    if not launched:
        return Response(
            {
                "status": "already_running"
            }
        )

    return Response(
        {
            "status": "started",
            "daily_chronicle": (
                daily_chronicle
            ),
        }
    )


@api_view(["GET"])
def run_logs(request):
    """
    Return watcher logs together with the real watcher running state.

    W-024:
    Do not trust AutomationRun(status=RUNNING) directly because a process
    crash/restart can leave a stale RUNNING row behind.

    SubprocessWatcherLauncher.is_running() reconciles the database status
    against the PostgreSQL advisory lock.
    """

    # W-024 correction:
    # PostgreSQL advisory lock is the source of truth for whether the watcher
    # is actually running.
    is_running = SubprocessWatcherLauncher.is_running()

    log_path = os.path.join(
        settings.BASE_DIR,
        "watcher_latest.log",
    )

    try:
        with open(log_path, "r") as f:
            content = f.read()

        return Response({
            "logs": content,
            "is_running": is_running,
        })

    except FileNotFoundError:
        return Response({
            "logs": "",
            "is_running": is_running,
        })