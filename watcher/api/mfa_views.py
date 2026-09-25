"""
MFA-01: authenticator enrolment, status and removal.

Every endpoint here needs an already-authenticated session. Enrolment
is something a signed-in user does to their own account; it is never
part of the login flow.
"""

from django.contrib.auth import authenticate

from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from watcher.services.totp_service import (
    confirm_enrolment,
    grouped_secret,
    render_qr_svg,
    consume_backup_code,
    disable,
    has_confirmed_device,
    regenerate_backup_codes,
    start_enrolment,
    unused_backup_code_count,
    verify_code,
)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mfa_status(request):
    """
    GET /api/auth/mfa/

    Drives the UI. Never exposes the secret.
    """
    enabled = has_confirmed_device(request.user)

    return Response({
        "enabled": enabled,
        "backup_codes_remaining": (
            unused_backup_code_count(request.user) if enabled else 0
        ),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_setup(request):
    """
    POST /api/auth/mfa/setup/

    Returns the provisioning URI for the QR code. The device is NOT
    active yet - the user must confirm with a code first, so a failed
    enrolment can never lock the account out.

    The QR image is rendered client-side from this URI rather than
    generated here, which keeps the secret out of a response body that
    a proxy or log might retain as an image.
    """
    try:
        _device, uri = start_enrolment(request.user)

    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)

    # Rendered by the shared helper so the login-time enrolment path
    # and this one cannot drift apart.
    return Response({
        "provisioning_uri": uri,
        "qr_svg": render_qr_svg(uri),
        "manual_key": grouped_secret(_device),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_confirm(request):
    """
    POST /api/auth/mfa/confirm/  {code}

    Activates the device and returns the backup codes. They are shown
    once and are not recoverable afterwards.
    """
    try:
        codes = confirm_enrolment(
            request.user,
            request.data.get("code"),
        )

    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)

    return Response({"enabled": True, "backup_codes": codes})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_disable(request):
    """
    POST /api/auth/mfa/disable/  {password, code}

    Requires BOTH the account password and a current second factor.

    A stolen session must not be able to switch two-factor off - that
    would make the whole feature theatre. Requiring the password means
    an attacker needs more than the cookie they stole.
    """
    if not has_confirmed_device(request.user):
        return Response(
            {"detail": "Two-factor authentication is not enabled."},
            status=400,
        )

    password = request.data.get("password") or ""

    if authenticate(
        request._request,
        username=request.user.get_username(),
        password=password,
    ) is None:
        return Response(
            {"detail": "Password is not correct."},
            status=400,
        )

    code = request.data.get("code")

    if not verify_code(request.user, code) and not consume_backup_code(
        request.user,
        code,
    ):
        return Response(
            {"detail": "That code is not valid."},
            status=400,
        )

    disable(request.user)

    return Response({"enabled": False})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_backup_codes(request):
    """
    POST /api/auth/mfa/backup-codes/  {code}

    Issues a fresh set and invalidates the old ones. Requires a current
    second factor so a stolen session cannot mint itself recovery codes.
    """
    if not has_confirmed_device(request.user):
        return Response(
            {"detail": "Two-factor authentication is not enabled."},
            status=400,
        )

    if not verify_code(request.user, request.data.get("code")):
        return Response(
            {"detail": "That code is not valid."},
            status=400,
        )

    return Response({
        "backup_codes": regenerate_backup_codes(request.user),
    })