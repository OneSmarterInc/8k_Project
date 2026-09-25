"""
W-010: token login for the React frontend.

POST /api/auth/login/   {username, password} -> {token, username, is_staff}
GET  /api/auth/me/      -> {username, is_staff}
POST /api/auth/logout/  -> deletes the caller's token
"""

from django.contrib.auth import authenticate, get_user_model
from django.core import signing
from django.views.decorators.csrf import ensure_csrf_cookie

from rest_framework.authtoken.models import Token
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from watcher.api.authentication import (
    clear_auth_cookie,
    get_valid_token,
    set_auth_cookie,
)

from .throttles import (
    LoginIPRateThrottle,
    LoginUsernameRateThrottle,
    MFAVerifyRateThrottle,
)
from watcher.services.totp_service import (
    consume_backup_code,
    has_confirmed_device,
    verify_code,
)

# MFA-01: the handle between step 1 and step 2 of login.
#
# A signed, timestamped user id rather than a database row: tamper
# proof, self-expiring, nothing to clean up. Five minutes is long
# enough to open an authenticator app.
MFA_SALT = "watcher.mfa.login"
MFA_TOKEN_MAX_AGE = 300


def _user_payload(user):
    return {
        "username": user.get_username(),
        "is_staff": bool(user.is_staff),
    }


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginIPRateThrottle, LoginUsernameRateThrottle])
def login(request):
    username = str(request.data.get("username") or "").strip()
    password = str(request.data.get("password") or "")

    if not username or not password:
        return Response(
            {"detail": "Username and password are required."},
            status=400,
        )

    user = authenticate(
        request._request,
        username=username,
        password=password,
    )

    if user is None or not user.is_active:
        return Response(
            {"detail": "Invalid username or password."},
            status=400,
        )

    # MFA-01: a user with a confirmed authenticator must present a
    # second factor. NOTHING is issued here - no token, no cookie - so
    # a stolen password alone gets only a short-lived signed handle.
    #
    # A user WITHOUT a confirmed device falls straight through to the
    # original response below, byte for byte.
    if has_confirmed_device(user):
        return Response({
            "mfa_required": True,
            "mfa_token": signing.TimestampSigner(
                salt=MFA_SALT,
            ).sign(str(user.pk)),
        })

    # Security review #3: reuse a still-valid token, replace an expired one.
    token = get_valid_token(user)

    # I-07: the token now travels in an HttpOnly cookie. It is STILL
    # returned in the body so any existing client, script or test that
    # reads "token" keeps working. The browser app no longer stores it.
    response = Response({"token": token.key, **_user_payload(user)})

    return set_auth_cookie(response, token)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(_user_payload(request.user))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout(request):
    Token.objects.filter(user=request.user).delete()

    # I-07: clear the cookie too, or the browser keeps sending a key
    # that no longer resolves and every request 401s until it expires.
    response = Response({"status": "logged_out"})

    return clear_auth_cookie(response)

@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf(request):
    """
    GET /api/auth/csrf/

    P-05: hand the browser a csrftoken cookie so the axios client can
    echo it back in X-CSRFToken on the first POST.

    Called once at app start, before login. AllowAny by necessity: the
    token is needed to log in. It is not a secret - its value only
    proves the request came from a page the browser actually loaded
    from this origin.
    """
    return Response({"detail": "CSRF cookie set."})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginIPRateThrottle, MFAVerifyRateThrottle])
def login_verify(request):
    """
    POST /api/auth/login/verify/  {mfa_token, code}

    Step 2 of login. `code` is either a six-digit authenticator code or
    one of the backup codes issued at enrolment.

    Throttled: six digits is a million combinations, which is trivially
    brute-forceable without a limit.
    """
    mfa_token = request.data.get("mfa_token") or ""
    code = request.data.get("code") or ""

    try:
        user_pk = signing.TimestampSigner(salt=MFA_SALT).unsign(
            mfa_token,
            max_age=MFA_TOKEN_MAX_AGE,
        )

    except signing.SignatureExpired:
        return Response(
            {"detail": "That took too long. Please sign in again."},
            status=400,
        )

    except signing.BadSignature:
        return Response(
            {"detail": "Invalid sign-in attempt."},
            status=400,
        )

    user = get_user_model().objects.filter(pk=user_pk).first()

    if user is None or not user.is_active:
        return Response(
            {"detail": "Invalid sign-in attempt."},
            status=400,
        )

    # A backup code is longer than six digits, so trying the
    # authenticator first costs nothing and keeps the common path fast.
    if not verify_code(user, code) and not consume_backup_code(user, code):
        return Response(
            {"detail": "That code is not valid."},
            status=400,
        )

    token = get_valid_token(user)

    response = Response({"token": token.key, **_user_payload(user)})

    return set_auth_cookie(response, token)