"""SMTP configuration endpoint (W-034).

Split out of the former single-file watcher/api/views.py. Behaviour is
unchanged.
"""

from django.conf import settings
from django.core.mail import EmailMessage

from rest_framework.decorators import permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from watcher.models import SMTPConfig
from watcher.services.smtp_settings import (
    ALLOWED_SMTP_PORTS,
    get_smtp_settings,
)

from ..serializers import SMTPConfigSerializer


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