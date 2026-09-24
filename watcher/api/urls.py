from django.urls import path
from . import auth_views

from .views import (
    SMTPConfigView,
    ScheduleConfigView,
    filings,
    resolve_amendment,
    run_logs,
    runs,
    trigger_run,
)



urlpatterns = [
    # W-010: token login
    path("auth/login/", auth_views.login, name="auth_login"),
    path("auth/me/", auth_views.me, name="auth_me"),
    path("auth/logout/", auth_views.logout, name="auth_logout"),

    # ... your existing paths stay below ...
    path(
        "filings/",
        filings,
        name="filings",
    ),
    path(
        "filings/<int:filing_id>/resolve-amendment/",
        resolve_amendment,
        name="resolve_amendment",
    ),
    path(
        "runs/",
        runs,
        name="runs",
    ),
    path(
        "runs/trigger/",
        trigger_run,
        name="trigger_run",
    ),
    path(
        "runs/logs/",
        run_logs,
        name="run_logs",
    ),
    path(
        "settings/smtp/",
        SMTPConfigView.as_view(),
        name="smtp_config",
    ),
    path(
        "settings/schedule/",
        ScheduleConfigView.as_view(),
        name="schedule_config",
    ),
]