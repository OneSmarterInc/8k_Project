"""
W-034: single source of SMTP settings for the whole app.

Order of precedence:
  1. The SMTPConfig row saved from the UI (host, port, sender, ...).
  2. If no row exists yet, the SMTP_* values from settings/.env.

The password comes from SMTP_PASSWORD in backend/.env. It can be set
from the UI (admin-only): the API writes ONLY that one line in .env and
never returns the password. It is read fresh from .env every time, so
all processes use the latest value without a restart. If .env has no
SMTP_PASSWORD line, the SMTP_PASSWORD environment variable is used.
"""

import os
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import get_connection

from watcher.services.env_file import SMTP_PASSWORD_KEY, read_env_value

# Outbound SMTP ports the server is allowed to connect to.
ALLOWED_SMTP_PORTS = frozenset({25, 465, 587, 2525})


@dataclass(frozen=True)
class SMTPSettings:
    host: str
    port: int
    security: str
    username: str
    sender_name: str
    sender_email: str
    reply_to_email: str

    # Set only for a one-off connection test with a typed password.
    override_password: str = ""

    @property
    def password(self):
        if self.override_password:
            return self.override_password
        # Fresh from .env on every use (web server, watcher subprocess
        # and scheduler all see the latest saved password).
        from_file = read_env_value(SMTP_PASSWORD_KEY)
        if from_file is not None:
            return from_file
        return os.environ.get(SMTP_PASSWORD_KEY, "")

    @property
    def use_ssl(self):
        return self.security == "SSL"

    @property
    def use_tls(self):
        return self.security in {"TLS", "STARTTLS"}

    @property
    def from_address(self):
        if self.sender_name:
            return f"{self.sender_name} <{self.sender_email}>"
        return self.sender_email

    def connection(self, timeout=10):
        """Django mail connection built from these settings."""
        return get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=self.host,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            use_tls=self.use_tls,
            use_ssl=self.use_ssl,
            timeout=timeout,
        )


def get_smtp_settings():
    """Return the active SMTP settings (DB row first, then settings)."""
    from watcher.models import SMTPConfig

    row = SMTPConfig.objects.filter(pk=1).first()
    if row is not None:
        return SMTPSettings(
            host=row.host,
            port=row.port,
            security=row.security,
            username=row.username,
            sender_name=row.sender_name,
            sender_email=row.sender_email,
            reply_to_email=row.reply_to_email,
        )

    return SMTPSettings(
        host=getattr(settings, "EMAIL_HOST", ""),
        port=int(getattr(settings, "EMAIL_PORT", 587) or 587),
        security=getattr(settings, "SMTP_SECURITY", "STARTTLS"),
        username=getattr(settings, "EMAIL_HOST_USER", ""),
        sender_name=getattr(settings, "SEC_EMAIL_SENDER_NAME", ""),
        sender_email=getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        reply_to_email=getattr(settings, "SEC_REPLY_TO_EMAIL", ""),
    )