"""W-034: SMTP settings can no longer rewrite .env or probe other hosts.

The SMTP password can be set from the UI: it is written as the single
SMTP_PASSWORD line in .env (nothing else in .env changes), never
returned, and a blank value keeps the current one.

Every test here points SMTP_ENV_FILE at a temporary file, so the real
backend/.env is never touched.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from watcher.models import SMTPConfig
from watcher.services.env_file import (
    read_env_value,
    write_env_value,
)
from watcher.services.smtp_settings import get_smtp_settings

URL = "/api/settings/smtp/"

VALID = {
    "senderName": "SEC Watcher",
    "smtpHost": "smtp.example.com",
    "smtpPort": 587,
    "securityProtocol": "STARTTLS",
    "smtpUsername": "watcher@example.com",
    "senderEmail": "watcher@example.com",
    "replyToEmail": "",
}


class TempEnvMixin:
    """Point SMTP_ENV_FILE at a throwaway .env for each test."""

    ENV_TEMPLATE = (
        "# backend settings\n"
        "DB_HOST=localhost\n"
        "ENTRY_RULE=T_PLUS_1\n"
        "SMTP_PASSWORD=old-env-pass\n"
        "DJANGO_DEBUG=True\n"
    )

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.mkdtemp()
        self.tmp_env = Path(self._tmpdir) / ".env"
        self.tmp_env.write_text(self.ENV_TEMPLATE, encoding="utf-8")
        override = override_settings(SMTP_ENV_FILE=str(self.tmp_env))
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self._tmpdir, True)
        # Keep this process's environment clean between tests.
        saved = os.environ.pop("SMTP_PASSWORD", None)
        self.addCleanup(
            lambda: os.environ.__setitem__("SMTP_PASSWORD", saved)
            if saved is not None else os.environ.pop("SMTP_PASSWORD", None)
        )


class SMTPConfigApiTests(TempEnvMixin, TestCase):

    def setUp(self):
        super().setUp()
        admin = get_user_model().objects.create_user(
            username="admin", password="pw-123-admin", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(admin)

        self.env_path = Path(settings.BASE_DIR) / ".env"
        self.env_before = (
            self.env_path.read_bytes() if self.env_path.exists() else None
        )

    def _env_unchanged(self):
        after = self.env_path.read_bytes() if self.env_path.exists() else None
        self.assertEqual(after, self.env_before, ".env must never be written")

    def test_valid_save_goes_to_database_not_env(self):
        response = self.client.post(f"{URL}?action=save", VALID, format="json")
        self.assertEqual(response.status_code, 200)
        row = SMTPConfig.objects.get(pk=1)
        self.assertEqual(row.host, "smtp.example.com")
        self.assertEqual(row.port, 587)
        self._env_unchanged()

    def test_newline_injection_is_rejected(self):
        payload = dict(
            VALID, smtpHost="smtp.x.com\nDB_HOST=evil\nENTRY_RULE=SAME_SESSION"
        )
        response = self.client.post(f"{URL}?action=save", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SMTPConfig.objects.exists())
        self._env_unchanged()

    def test_newline_in_any_text_field_is_rejected(self):
        payload = dict(VALID, senderName="Bob\r\nSMTP_PASSWORD=x")
        response = self.client.post(f"{URL}?action=save", payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_hostname_is_rejected(self):
        payload = dict(VALID, smtpHost="smtp example com")
        response = self.client.post(f"{URL}?action=save", payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_non_smtp_port_is_rejected(self):
        payload = dict(VALID, smtpPort=5432)
        response = self.client.post(f"{URL}?action=save", payload, format="json")
        self.assertEqual(response.status_code, 400)

    # --- password from the UI -> SMTP_PASSWORD in .env ---------------

    def _save(self, **extra):
        return self.client.post(
            f"{URL}?action=save", dict(VALID, **extra), format="json"
        )

    def test_password_is_written_to_env_file(self):
        response = self._save(smtpPassword="New-Pass-123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(read_env_value("SMTP_PASSWORD"), "New-Pass-123")
        self.assertEqual(get_smtp_settings().password, "New-Pass-123")
        self._env_unchanged()  # the REAL .env is untouched

    def test_only_the_password_line_changes(self):
        self._save(smtpPassword="New-Pass-123")
        lines = self.tmp_env.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines,
            [
                "# backend settings",
                "DB_HOST=localhost",
                "ENTRY_RULE=T_PLUS_1",
                "DJANGO_DEBUG=True",
                'SMTP_PASSWORD="New-Pass-123"',
            ],
        )

    def test_password_is_read_back_exactly(self):
        tricky = '  a"b #c =d\'e  '
        self._save(smtpPassword=tricky)
        self.assertEqual(read_env_value("SMTP_PASSWORD"), tricky)

    def test_blank_password_keeps_the_current_one(self):
        self._save(smtpPassword="")
        self.assertEqual(read_env_value("SMTP_PASSWORD"), "old-env-pass")

    def test_missing_password_field_keeps_the_current_one(self):
        self._save()
        self.assertEqual(read_env_value("SMTP_PASSWORD"), "old-env-pass")

    def test_injection_through_password_is_rejected(self):
        before = self.tmp_env.read_bytes()
        response = self._save(
            smtpPassword="x\nDB_HOST=evil\nENTRY_RULE=SAME_SESSION"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.tmp_env.read_bytes(), before)
        self.assertFalse(SMTPConfig.objects.exists())

    def test_control_characters_in_password_are_rejected(self):
        response = self._save(smtpPassword="abc\x00def")
        self.assertEqual(response.status_code, 400)

    def test_overlong_password_is_rejected(self):
        response = self._save(smtpPassword="x" * 257)
        self.assertEqual(response.status_code, 400)

    def test_get_never_returns_the_password(self):
        self._save(smtpPassword="ui-secret-123")
        data = self.client.get(URL).json()
        self.assertTrue(data["passwordConfigured"])
        self.assertNotIn("ui-secret-123", str(data))

    def test_unwritable_env_returns_error_and_saves_nothing(self):
        with patch(
            "watcher.api.views.smtp.write_env_value",
            side_effect=__import__(
                "watcher.services.env_file", fromlist=["EnvFileError"]
            ).EnvFileError("Cannot write .env: permission denied"),
        ):
            response = self._save(smtpPassword="New-Pass-123")
        self.assertEqual(response.status_code, 500)
        self.assertIn("permission denied", response.json()["message"])
        self.assertFalse(SMTPConfig.objects.exists())

    def _mock_connection(self):
        from django.core.mail.backends.locmem import EmailBackend
        mocker = patch("watcher.services.smtp_settings.get_connection")
        started = mocker.start()
        started.return_value = EmailBackend()
        self.addCleanup(mocker.stop)
        return started

    def test_test_uses_typed_password_but_saved_host(self):
        self._save(smtpPassword="saved-pass")
        conn = self._mock_connection()
        response = self.client.post(
            f"{URL}?action=test",
            {"smtpPassword": "typed-pass", "smtpHost": "10.0.0.5"},
            format="json",
        )
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(conn.call_args.kwargs["password"], "typed-pass")
        self.assertEqual(conn.call_args.kwargs["host"], "smtp.example.com")
        # Testing never changes the saved password.
        self.assertEqual(read_env_value("SMTP_PASSWORD"), "saved-pass")

    def test_test_uses_saved_password_when_none_typed(self):
        self._save(smtpPassword="saved-pass")
        conn = self._mock_connection()
        self.client.post(f"{URL}?action=test", {}, format="json")
        self.assertEqual(conn.call_args.kwargs["password"], "saved-pass")

    def test_test_rejects_newline_in_typed_password(self):
        self._save()
        response = self.client.post(
            f"{URL}?action=test", {"smtpPassword": "a\r\nb"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_second_save_updates_the_single_row(self):
        self.client.post(f"{URL}?action=save", VALID, format="json")
        self.client.post(
            f"{URL}?action=save",
            dict(VALID, smtpHost="mail.example.org"),
            format="json",
        )
        self.assertEqual(SMTPConfig.objects.count(), 1)
        self.assertEqual(SMTPConfig.objects.get().host, "mail.example.org")

    def test_get_never_returns_a_password(self):
        self.client.post(f"{URL}?action=save", VALID, format="json")
        with patch.dict(os.environ, {"SMTP_PASSWORD": "secret"}):
            data = self.client.get(URL).json()
        self.assertEqual(data["smtpHost"], "smtp.example.com")
        self.assertTrue(data["passwordConfigured"])
        self.assertNotIn("secret", str(data))

    def test_test_email_uses_saved_host_not_request_host(self):
        self.client.post(f"{URL}?action=save", VALID, format="json")

        with patch("watcher.services.smtp_settings.get_connection") as mock_conn:
            from django.core.mail.backends.locmem import EmailBackend
            mock_conn.return_value = EmailBackend()

            response = self.client.post(
                f"{URL}?action=test",
                {"smtpHost": "10.0.0.5", "smtpPort": 22},
                format="json",
            )

        self.assertEqual(response.json()["status"], "success")
        kwargs = mock_conn.call_args.kwargs
        self.assertEqual(kwargs["host"], "smtp.example.com")
        self.assertEqual(kwargs["port"], 587)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["watcher@example.com"])

    def test_test_email_requires_saved_settings(self):
        with override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL=""):
            response = self.client.post(f"{URL}?action=test", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_unknown_action_is_rejected(self):
        response = self.client.post(f"{URL}?action=wipe", {}, format="json")
        self.assertEqual(response.status_code, 400)


class SMTPSettingsSourceTests(TempEnvMixin, TestCase):

    @override_settings(
        EMAIL_HOST="env.example.com",
        EMAIL_PORT=2525,
        DEFAULT_FROM_EMAIL="env@example.com",
    )
    def test_falls_back_to_env_settings_when_no_row(self):
        smtp = get_smtp_settings()
        self.assertEqual(smtp.host, "env.example.com")
        self.assertEqual(smtp.port, 2525)

    def test_saved_row_wins_over_env(self):
        SMTPConfig.objects.create(
            pk=1, host="db.example.com", port=465, security="SSL",
            sender_email="db@example.com",
        )
        smtp = get_smtp_settings()
        self.assertEqual(smtp.host, "db.example.com")
        self.assertTrue(smtp.use_ssl)
        self.assertFalse(smtp.use_tls)

    def test_password_read_fresh_from_env_file(self):
        # Another process updating .env is seen immediately (no restart),
        # even if this process still has an old value in its environment.
        with patch.dict(os.environ, {"SMTP_PASSWORD": "stale-in-memory"}):
            write_env_value("SMTP_PASSWORD", "fresh-in-file")
            os.environ["SMTP_PASSWORD"] = "stale-in-memory"
            self.assertEqual(get_smtp_settings().password, "fresh-in-file")

    def test_environment_is_fallback_when_env_file_has_no_password(self):
        write_env_value("SMTP_PASSWORD", None)  # remove the line
        with patch.dict(os.environ, {"SMTP_PASSWORD": "from-os-env"}):
            self.assertEqual(get_smtp_settings().password, "from-os-env")

    def test_crlf_and_bom_are_preserved(self):
        self.tmp_env.write_bytes(
            b"\xef\xbb\xbfDB_HOST=localhost\r\nSMTP_PASSWORD=old\r\n"
        )
        write_env_value("SMTP_PASSWORD", "new")
        self.assertEqual(
            self.tmp_env.read_bytes(),
            b'\xef\xbb\xbfDB_HOST=localhost\r\nSMTP_PASSWORD="new"\r\n',
        )

    def test_creates_env_file_if_missing(self):
        self.tmp_env.unlink()
        write_env_value("SMTP_PASSWORD", "brand-new")
        self.assertEqual(read_env_value("SMTP_PASSWORD"), "brand-new")