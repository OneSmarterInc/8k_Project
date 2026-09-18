from django.urls import path
from .views import filings, runs, trigger_run, run_logs, SMTPConfigView

urlpatterns = [
    path(
        "filings/",
        filings,
        name="filings"
    ),
    path(
        "runs/",
        runs,
        name="runs"
    ),
    path(
        "runs/trigger/",
        trigger_run,
        name="trigger_run"
    ),
    path(
        "runs/logs/",
        run_logs,
        name="run_logs"
    ),
    path(
        "settings/smtp/",
        SMTPConfigView.as_view(),
        name="smtp_config"
    ),
]