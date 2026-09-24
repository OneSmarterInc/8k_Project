"""W-034: SMTP settings can no longer rewrite .env or probe other hosts."""

import os
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from watcher.models import SMTPConfig
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


class SMTPConfigApiTests(TestCase):

    def setUp(self):
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

    def test_password_in_request_is_ignored(self):
        payload = dict(VALID, smtpPassword="should-not-be-stored")
        response = self.client.post(f"{URL}?action=save", payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(hasattr(SMTPConfig.objects.get(pk=1), "password"))
        self._env_unchanged()

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


class SMTPSettingsSourceTests(TestCase):

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

    def test_password_only_from_environment(self):
        with patch.dict(os.environ, {"SMTP_PASSWORD": "from-env"}):
            self.assertEqual(get_smtp_settings().password, "from-env")