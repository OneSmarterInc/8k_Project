from django.urls import path
from . import auth_views, mfa_views

from .views import (
    SMTPConfigView,
    ScheduleConfigView,
    filings,
    # resolve_amendment,  # 8-K/A DISABLED
    queue_export_download,
    queue_export_regenerate,
    queue_exports,
    run_logs,
    runs,
    trigger_run,
    labelling_labels,
    labelling_next,
    override_classification,
    taxonomy,
)



urlpatterns = [
    # W-010: token login
    # P-05: issues the csrftoken cookie. Must be reachable before login.
    path("auth/csrf/", auth_views.csrf, name="auth_csrf"),
    path("auth/login/", auth_views.login, name="auth_login"),
    # MFA-01: step 2 of login for users with an authenticator.
    path(
        "auth/login/verify/",
        auth_views.login_verify,
        name="auth_login_verify",
    ),

    # MFA-02: forced first-time enrolment, before any session exists.
    # Declared BEFORE nothing else matches these paths, so order is
    # not load-bearing here, but they must stay above any catch-all.
    path(
        "auth/login/enrol/",
        auth_views.login_enrol,
        name="auth_login_enrol",
    ),
    path(
        "auth/login/enrol/confirm/",
        auth_views.login_enrol_confirm,
        name="auth_login_enrol_confirm",
    ),
    path("auth/me/", auth_views.me, name="auth_me"),
    path("auth/logout/", auth_views.logout, name="auth_logout"),

    # MFA-01: authenticator enrolment. All require a signed-in session.
    path("auth/mfa/", mfa_views.mfa_status, name="mfa_status"),
    path("auth/mfa/setup/", mfa_views.mfa_setup, name="mfa_setup"),
    path("auth/mfa/confirm/", mfa_views.mfa_confirm, name="mfa_confirm"),
    path("auth/mfa/disable/", mfa_views.mfa_disable, name="mfa_disable"),
    path(
        "auth/mfa/backup-codes/",
        mfa_views.mfa_backup_codes,
        name="mfa_backup_codes",
    ),

    # ... your existing paths stay below ...
    path(
        "filings/",
        filings,
        name="filings",
    ),
    # 8-K/A DISABLED: manual amendment resolution removed.
    # path(
    #     "filings/<int:filing_id>/resolve-amendment/",
    #     resolve_amendment,
    #     name="resolve_amendment",
    # ),
    # W-038: capture queue exports. Read-only listing and download.
    path(
        "queue/exports/",
        queue_exports,
        name="queue_exports",
    ),
    # Regenerate must be declared BEFORE the <str:filename> route,
    # otherwise "regenerate" is captured as a filename and 404s.
    path(
        "queue/exports/regenerate/",
        queue_export_regenerate,
        name="queue_export_regenerate",
    ),
    path(
        "queue/exports/<str:filename>/",
        queue_export_download,
        name="queue_export_download",
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
    # Guide Part 5: Interpreter review queue (staff only).
    path(
        "classifications/<int:classification_id>/override/",
        override_classification,
        name="override_classification",
    ),
    path(
        "taxonomy/",
        taxonomy,
        name="taxonomy",
    ),
    # Guide 4.2: blind labelling for ground truth (G2).
    path(
        "labelling/next/",
        labelling_next,
        name="labelling_next",
    ),
    path(
        "labelling/labels/",
        labelling_labels,
        name="labelling_labels",
    ),
    path(
        "settings/schedule/",
        ScheduleConfigView.as_view(),
        name="schedule_config",
    ),
]