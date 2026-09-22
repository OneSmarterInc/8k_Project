from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

from watcher.models import (
    Filing,
    AutomationRun,
    ScheduleConfig,
)
from watcher.services.watcher_launcher import (
    SubprocessWatcherLauncher,
)

from .serializers import (
    FilingSerializer,
    AutomationRunSerializer,
)

import os
import smtplib

from email.message import EmailMessage


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


@csrf_exempt
@api_view(["POST"])
def trigger_run(request):
    launched = SubprocessWatcherLauncher.launch()

    if not launched:
        return Response({
            "status": "already_running"
        })

    return Response({
        "status": "started"
    })


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


@api_view(["GET"])
def filings(request):
    queryset = (
        Filing.objects
        .select_related("company")
        .order_by("-created_at")
    )

    import datetime

    start_date = datetime.datetime(
        2026,
        9,
        14,
        tzinfo=datetime.timezone.utc,
    )

    status_param = request.GET.get("status")

    if status_param == "failed":
        queryset = queryset.filter(
            failure_events__isnull=False,
            failure_events__resolved_at__isnull=True,
        ).distinct()

    else:
        # By default, only show filings that successfully generated a summary
        queryset = queryset.filter(
            summary_cache__isnull=False
        ).exclude(
            failure_events__isnull=False,
            failure_events__resolved_at__isnull=True,
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

    serializer = FilingSerializer(
        queryset,
        many=True,
    )

    return Response(serializer.data)


class SMTPConfigView(APIView):

    def get(self, request):
        env_path = os.path.join(
            settings.BASE_DIR,
            ".env",
        )

        config = {
            "senderName": "",
            "smtpHost": "",
            "smtpPort": "587",
            "securityProtocol": "TLS",
            "smtpUsername": "",
            "senderEmail": "",
            "replyToEmail": "",
        }

        if os.path.exists(env_path):
            with open(
                env_path,
                "r",
                encoding="utf-8-sig",
            ) as f:

                for line in f:
                    if (
                        "=" in line
                        and not line.strip().startswith("#")
                    ):
                        k, v = line.strip().split(
                            "=",
                            1,
                        )

                        k = k.strip()
                        v = v.strip()

                        if k == "SMTP_SENDER_NAME":
                            config["senderName"] = v

                        elif k == "SMTP_HOST":
                            config["smtpHost"] = v

                        elif k == "SMTP_PORT":
                            config["smtpPort"] = v

                        elif k == "SMTP_SECURITY":
                            config["securityProtocol"] = v

                        elif k == "SMTP_USERNAME":
                            config["smtpUsername"] = v

                        elif k == "SMTP_SENDER_EMAIL":
                            config["senderEmail"] = v

                        elif k == "SMTP_REPLY_TO_EMAIL":
                            config["replyToEmail"] = v

        return Response(config)

    def post(self, request):
        action = request.GET.get("action")
        data = request.data

        if action == "test":
            host = data.get("smtpHost")

            port = int(
                data.get(
                    "smtpPort",
                    587,
                )
            )

            user = data.get(
                "smtpUsername"
            )

            password = data.get(
                "smtpPassword"
            )

            sender = data.get(
                "senderEmail"
            )

            # Send to self for test
            recipient = data.get(
                "senderEmail"
            )

            msg = EmailMessage()

            msg.set_content(
                "This is a test email from the SEC Agentic Watcher "
                "to verify SMTP settings."
            )

            msg["Subject"] = (
                "Test Email - SEC Watcher"
            )

            msg["From"] = sender
            msg["To"] = recipient

            try:
                server = smtplib.SMTP(
                    host,
                    port,
                    timeout=10,
                )

                if (
                    data.get("securityProtocol") == "TLS"
                    or data.get("securityProtocol") == "STARTTLS"
                ):
                    server.starttls()

                # Existing SMTP behaviour intentionally preserved.
                server.login(
                    user,
                    password,
                )

                server.send_message(msg)
                server.quit()

                return Response({
                    "status": "success",
                    "message": (
                        "Test email sent successfully!"
                    ),
                })

            except Exception as e:
                return Response({
                    "status": "error",
                    "message": str(e),
                })

        elif action == "save":
            env_path = os.path.join(
                settings.BASE_DIR,
                ".env",
            )

            # Read existing
            lines = []

            if os.path.exists(env_path):
                with open(
                    env_path,
                    "r",
                    encoding="utf-8-sig",
                ) as f:
                    lines = f.readlines()

            # Prepare updates mapping
            updates = {
                "SMTP_SENDER_NAME": data.get(
                    "senderName"
                ),
                "SMTP_HOST": data.get(
                    "smtpHost"
                ),
                "SMTP_PORT": data.get(
                    "smtpPort"
                ),
                "SMTP_SECURITY": data.get(
                    "securityProtocol"
                ),
                "SMTP_USERNAME": data.get(
                    "smtpUsername"
                ),
                "SMTP_SENDER_EMAIL": data.get(
                    "senderEmail"
                ),
                "SMTP_REPLY_TO_EMAIL": data.get(
                    "replyToEmail"
                ),
            }

            if data.get("smtpPassword"):
                updates["SMTP_PASSWORD"] = (
                    data.get("smtpPassword")
                )

            new_lines = []
            updated_keys = set()

            for line in lines:
                if (
                    "=" in line
                    and not line.strip().startswith("#")
                ):
                    k, _ = line.split(
                        "=",
                        1,
                    )

                    k = k.strip()

                    if k in updates:
                        val = (
                            updates[k]
                            if updates[k] is not None
                            else ""
                        )

                        new_lines.append(
                            f"{k}={val}\n"
                        )

                        updated_keys.add(k)
                        continue

                new_lines.append(line)

            # Add missing keys
            for k, v in updates.items():
                if (
                    k not in updated_keys
                    and v
                ):
                    if (
                        not new_lines
                        or not new_lines[-1].endswith("\n")
                    ):
                        new_lines.append("\n")

                    new_lines.append(
                        f"{k}={v}\n"
                    )

            with open(
                env_path,
                "w",
                encoding="utf-8-sig",
            ) as f:
                f.writelines(new_lines)

            return Response({
                "status": "success",
                "message": (
                    "Configuration saved to .env"
                ),
            })

        return Response(
            {
                "status": "error",
                "message": "Invalid action",
            },
            status=400,
        )


class ScheduleConfigView(APIView):

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