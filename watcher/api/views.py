import os

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404

from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import (
    IsAdminUser,
    IsAuthenticated,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from watcher.models import (
    Filing,
    AutomationRun,
    ScheduleConfig,
    SMTPConfig,
)
from watcher.knowledge_base.models import FailureEvent
from watcher.knowledge_base.ingestion.amendment_linker import (
    AMBIGUOUS_AMENDMENT_TARGET,
    candidate_originals,
)
from watcher.services.smtp_settings import (
    ALLOWED_SMTP_PORTS,
    get_smtp_settings,
)
from watcher.services.watcher_launcher import (
    SubprocessWatcherLauncher,
)

from .serializers import (
    FilingSerializer,
    AutomationRunSerializer,
    SMTPConfigSerializer,
)


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


class FilingPagination(PageNumberPagination):
    """
    W-035: opt-in pagination for /api/filings/.

    Used only when the caller sends ?page= or ?page_size=, so every
    existing caller that expects a plain JSON list keeps getting one.
    """

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


@api_view(["GET"])
def filings(request):
    """
    status parameter:
        (none)      - all captured filings, except those with an
                      unresolved failure (those live in the Review Queue).
                      Includes filings that have no summary yet.
        summarized  - previous default: summary exists, no unresolved failure.
        failed      - unresolved failures only.
        review      - failures + ambiguous 8-K/A (Review Queue, W-022).
        flagged     - filings the Watcher flagged (flag=True).
        all         - every captured filing.
    """

    queryset = (
        Filing.objects
        # Performance only: load related rows in a few batched queries
        # instead of 3-4 queries per filing. Output is unchanged.
        .select_related("company", "summary_cache", "amends")
        .prefetch_related(
            "amended_by",
            Prefetch(
                "failure_events",
                queryset=(
                    FailureEvent.objects
                    .filter(resolved_at__isnull=True)
                    .order_by("-created_at")
                ),
                to_attr="prefetched_unresolved_failures",
            ),
        )
        .order_by("-created_at", "-id")
    )

    unresolved_failure = Q(
        failure_events__isnull=False,
        failure_events__resolved_at__isnull=True,
    )

    status_param = request.GET.get("status")

    if status_param == "failed":
        queryset = queryset.filter(
            unresolved_failure
        ).distinct()

    elif status_param == "review":
        # W-022:
        # Review Queue contains both:
        #
        # 1. filings with unresolved processing failures, and
        # 2. ambiguous 8-K/A filings requiring manual linking.
        queryset = queryset.filter(
            unresolved_failure
            | Q(
                form="8-K/A",
                amends__isnull=True,
                flag=True,
                flag_reason=(
                    AMBIGUOUS_AMENDMENT_TARGET
                ),
            )
        ).distinct()

    elif status_param == "summarized":
        queryset = queryset.filter(
            summary_cache__isnull=False
        ).exclude(
            unresolved_failure
        )

    elif status_param == "flagged":
        queryset = queryset.filter(
            flag=True
        )

    elif status_param == "all":
        pass

    else:
        # W-035: default shows every captured filing, summarized or not,
        # except unresolved failures (shown in the Review Queue instead).
        queryset = queryset.exclude(
            unresolved_failure
        )

    ticker = request.GET.get("ticker")
    form = request.GET.get("form")

    if ticker:
        queryset = queryset.filter(
            company__ticker__iexact=ticker
        )

    if form:
        queryset = queryset.filter(
            form=form
        )

    # W-035: paginate only when explicitly requested.
    if "page" in request.GET or "page_size" in request.GET:
        paginator = FilingPagination()
        page = paginator.paginate_queryset(queryset, request)

        return paginator.get_paginated_response(
            FilingSerializer(page, many=True).data
        )

    serializer = FilingSerializer(
        queryset,
        many=True,
    )

    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def resolve_amendment(request, filing_id):
    """
    W-022:
    Allow an administrator to manually resolve an ambiguous
    8-K/A amendment.

    The selected original filing must satisfy the same candidate
    matching policy used by amendment_linker.py.
    """

    original_id = request.data.get(
        "original_id"
    )

    try:
        original_id = int(
            original_id
        )

    except (TypeError, ValueError):
        return Response(
            {
                "status": "error",
                "message": (
                    "original_id must be a valid "
                    "filing ID."
                ),
            },
            status=400,
        )

    with transaction.atomic():
        amendment = get_object_or_404(
            Filing.objects
            .select_for_update()
            .select_related("company"),
            pk=filing_id,
            form="8-K/A",
        )

        # Never allow a second request to overwrite
        # an already resolved amendment relationship.
        if amendment.amends_id is not None:
            return Response(
                {
                    "status": "error",
                    "message": (
                        "This amendment is already linked."
                    ),
                },
                status=409,
            )

        # Manual resolution is only valid for amendments
        # explicitly marked as ambiguous.
        if (
            not amendment.flag
            or amendment.flag_reason
            != AMBIGUOUS_AMENDMENT_TARGET
        ):
            return Response(
                {
                    "status": "error",
                    "message": (
                        "This amendment is not awaiting "
                        "ambiguous-target review."
                    ),
                },
                status=409,
            )

        # candidate_originals() enforces:
        #
        # - same company
        # - form == 8-K
        # - same report_date
        #
        # This prevents a reviewer/API caller from linking
        # an unrelated filing.
        original = get_object_or_404(
            candidate_originals(
                amendment
            ),
            pk=original_id,
        )

        amendment.amends = original
        amendment.flag = False
        amendment.flag_reason = ""

        amendment.save(
            update_fields=[
                "amends",
                "flag",
                "flag_reason",
                "updated_at",
            ]
        )

    return Response(
        {
            "status": "linked",
            "filing_id": amendment.id,
            "original_id": original.id,
            "amends": (
                original.accession_number
            ),
        }
    )


class SMTPConfigView(APIView):
    """
    W-034: SMTP settings for the UI.

    - Settings are stored in the SMTPConfig table, never in .env.
    - Every field is validated; newlines are rejected.
    - The password is never accepted or returned. It lives only in the
      SMTP_PASSWORD environment variable on the server.
    - "test" sends to the SAVED configuration only, never to a host
      supplied in the request, and only on SMTP ports.
    """
        # W-010: SMTP settings are admin-only.
    permission_classes = [IsAdminUser]

    def get(self, request):
        smtp = get_smtp_settings()

        return Response({
            "senderName": smtp.sender_name,
            "smtpHost": smtp.host,
            "smtpPort": str(smtp.port),
            "securityProtocol": smtp.security,
            "smtpUsername": smtp.username,
            "senderEmail": smtp.sender_email,
            "replyToEmail": smtp.reply_to_email,
            "passwordConfigured": bool(smtp.password),
        })

    def post(self, request):
        action = request.GET.get("action")

        if action == "save":
            return self._save(request)

        if action == "test":
            return self._test()

        return Response(
            {"status": "error", "message": "Invalid action"},
            status=400,
        )

    def _save(self, request):
        serializer = SMTPConfigSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "status": "error",
                    "message": "Invalid SMTP settings.",
                    "errors": serializer.errors,
                },
                status=400,
            )

        data = serializer.validated_data

        SMTPConfig.objects.update_or_create(
            pk=1,
            defaults={
                "sender_name": data["senderName"],
                "host": data["smtpHost"],
                "port": data["smtpPort"],
                "security": data["securityProtocol"],
                "username": data["smtpUsername"],
                "sender_email": data["senderEmail"],
                "reply_to_email": data.get("replyToEmail", ""),
            },
        )

        # Any smtpPassword in the request is deliberately ignored.
        return Response({
            "status": "success",
            "message": "SMTP settings saved.",
        })

    def _test(self):
        smtp = get_smtp_settings()

        if not smtp.host or not smtp.sender_email:
            return Response(
                {
                    "status": "error",
                    "message": "Save SMTP settings before testing.",
                },
                status=400,
            )

        if smtp.port not in ALLOWED_SMTP_PORTS:
            return Response(
                {
                    "status": "error",
                    "message": "Saved port is not an allowed SMTP port.",
                },
                status=400,
            )

        message = EmailMessage(
            subject="Test Email - SEC Watcher",
            body=(
                "This is a test email from the SEC Agentic Watcher "
                "to verify SMTP settings."
            ),
            from_email=smtp.from_address,
            to=[smtp.sender_email],
            connection=smtp.connection(timeout=10),
        )

        try:
            message.send(fail_silently=False)
        except Exception as exc:
            return Response({
                "status": "error",
                "message": f"Test email failed: {exc}",
            })

        return Response({
            "status": "success",
            "message": "Test email sent successfully!",
        })


class ScheduleConfigView(APIView):

    def get_permissions(self):
        # W-010: anyone logged in may read the schedule;
        # only admins may change it.
        if self.request.method == "POST":
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def get(self, request):
        config = ScheduleConfig.objects.first()

        if not config:
            return Response({})

        return Response({
            "frequency": config.frequency,
            "start_date": (
                config.start_date.isoformat()
                if config.start_date
                else None
            ),
            "start_time": (
                config.start_time.strftime("%H:%M")
                if config.start_time
                else None
            ),
            "sync_zone": config.sync_zone,
            "daily_recur": config.daily_recur,
            "weekly_recur": config.weekly_recur,
            "weekly_days": config.weekly_days,
            "monthly_type": config.monthly_type,
            "monthly_months": config.monthly_months,
            "monthly_days": config.monthly_days,
            "monthly_on_week": config.monthly_on_week,
            "monthly_on_day": config.monthly_on_day,
            "run_count": config.run_count,
            "run_times": config.run_times,
            "is_active": config.is_active,

            # W-037
            "interval_minutes": config.interval_minutes,
            "active_window_start": (
                config.active_window_start.strftime("%H:%M")
                if config.active_window_start
                else None
            ),
            "active_window_end": (
                config.active_window_end.strftime("%H:%M")
                if config.active_window_end
                else None
            ),
            "nightly_sweep": config.nightly_sweep,
        })

    def post(self, request):
        data = request.data

        config = ScheduleConfig.objects.first()

        if not config:
            config = ScheduleConfig()

        config.frequency = data.get(
            "frequency",
            "daily",
        )

        start_date_str = data.get(
            "start_date"
        )

        if start_date_str:
            import datetime

            config.start_date = (
                datetime.datetime.strptime(
                    start_date_str,
                    "%Y-%m-%d",
                ).date()
            )

        start_time_str = data.get(
            "start_time"
        )

        if start_time_str:
            import datetime

            config.start_time = (
                datetime.datetime.strptime(
                    start_time_str,
                    "%H:%M",
                ).time()
            )

        config.sync_zone = data.get(
            "sync_zone",
            False,
        )

        config.daily_recur = int(
            data.get(
                "daily_recur",
                1,
            )
        )

        config.weekly_recur = int(
            data.get(
                "weekly_recur",
                1,
            )
        )

        config.weekly_days = data.get(
            "weekly_days",
            {},
        )

        config.monthly_type = data.get(
            "monthly_type",
            "days",
        )

        config.monthly_months = data.get(
            "monthly_months",
            "All months",
        )

        config.monthly_days = data.get(
            "monthly_days",
            "1",
        )

        config.monthly_on_week = data.get(
            "monthly_on_week",
            "First",
        )

        config.monthly_on_day = data.get(
            "monthly_on_day",
            "Sunday",
        )

        config.run_count = int(
            data.get(
                "run_count",
                1,
            )
        )

        config.run_times = data.get(
            "run_times",
            [],
        )

        # ----------------------------------------------------------
        # W-037
        #
        # These four fall back to the STORED value, not to a constant.
        # Schedule.jsx posts the whole config from two places, and an
        # older frontend build will omit these keys. A hardcoded
        # fallback would silently reset the interval settings every
        # time the automation toggle is flipped.
        # ----------------------------------------------------------
        try:
            config.interval_minutes = int(
                data.get(
                    "interval_minutes",
                    config.interval_minutes or 20,
                )
            )
        except (TypeError, ValueError):
            config.interval_minutes = (
                config.interval_minutes or 20
            )

        config.nightly_sweep = bool(
            data.get(
                "nightly_sweep",
                config.nightly_sweep if config.pk else True,
            )
        )

        for window_field in (
            "active_window_start",
            "active_window_end",
        ):
            if window_field in data:
                import datetime

                raw_window = data.get(window_field)

                try:
                    setattr(
                        config,
                        window_field,
                        (
                            datetime.datetime.strptime(
                                raw_window,
                                "%H:%M",
                            ).time()
                            if raw_window
                            else None
                        ),
                    )
                except (TypeError, ValueError):
                    # Malformed time leaves the stored value intact
                    # rather than blanking the window.
                    pass

        new_is_active = data.get(
            "is_active",
            False,
        )

        if (
            config.is_active
            and not new_is_active
        ):
            from django.core.cache import cache

            cache.set(
                "abort_automation_run",
                True,
                timeout=300,
            )

        config.is_active = (
            new_is_active
        )

        config.save()

        return Response({
            "status": "success",
            "message": (
                "Schedule configuration saved."
            ),
        })