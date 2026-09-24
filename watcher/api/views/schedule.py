"""Schedule configuration endpoint (W-037 interval fields included).

Split out of the former single-file watcher/api/views.py. Behaviour is
unchanged.
"""

from django.conf import settings

from rest_framework.permissions import (
    IsAdminUser,
    IsAuthenticated,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from watcher.models import ScheduleConfig


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