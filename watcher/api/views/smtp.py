"""SMTP configuration endpoint (W-034).

Split out of the former single-file watcher/api/views.py.
"""

from dataclasses import replace

from django.core.mail import EmailMessage

from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from watcher.models import SMTPConfig
from watcher.services.env_file import (
    SMTP_PASSWORD_KEY,
    EnvFileError,
    is_safe_env_value,
    write_env_value,
)
from watcher.services.smtp_settings import (
    ALLOWED_SMTP_PORTS,
    get_smtp_settings,
)

from ..serializers import SMTPConfigSerializer


class SMTPConfigView(APIView):
    """
    W-034: SMTP settings for the UI.

    - Host, port, sender, etc. are stored in the SMTPConfig table.
    - Every field is validated; newlines are rejected.
    - The SMTP password entered in the UI is saved as SMTP_PASSWORD in
      backend/.env. ONLY that one line is written; the rest of .env is
      never touched. A blank password keeps the current one. The
      password is never returned by the API.
    - "test" connects to the SAVED host/port only, never to a host
      supplied in the request, and only on SMTP ports. It may use a
      password typed in the form (not yet saved) for that one test.
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
            # Never the password itself: only whether one is set.
            "passwordConfigured": bool(smtp.password),
        })

    def post(self, request):
        action = request.GET.get("action")

        if action == "save":
            return self._save(request)

        if action == "test":
            return self._test(request)

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
        new_password = data.get("smtpPassword", "")

        # Write the password first: if .env cannot be written, nothing
        # is saved and the admin sees a clear error.
        if new_password:
            try:
                write_env_value(SMTP_PASSWORD_KEY, new_password)
            except (ValueError, EnvFileError) as exc:
                return Response(
                    {
                        "status": "error",
                        "message": f"Could not save the SMTP password: {exc}",
                    },
                    status=500 if isinstance(exc, EnvFileError) else 400,
                )

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

        return Response({
            "status": "success",
            "message": "SMTP settings saved.",
        })

    def _test(self, request):
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

        # Optional: a password typed in the form but not saved yet.
        # Only the password is taken from the request; host and port
        # always come from the saved configuration.
        typed = request.data.get("smtpPassword") or ""

        if typed:
            if not is_safe_env_value(typed):
                return Response(
                    {"status": "error", "message": "Invalid password."},
                    status=400,
                )
            smtp = replace(smtp, override_password=typed)

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