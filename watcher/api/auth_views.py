"""
W-010: token login for the React frontend.

POST /api/auth/login/   {username, password} -> {token, username, is_staff}
GET  /api/auth/me/      -> {username, is_staff}
POST /api/auth/logout/  -> deletes the caller's token
"""

from django.contrib.auth import authenticate

from rest_framework.authtoken.models import Token
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response


def _user_payload(user):
    return {
        "username": user.get_username(),
        "is_staff": bool(user.is_staff),
    }


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
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

    token, _ = Token.objects.get_or_create(user=user)

    return Response({"token": token.key, **_user_payload(user)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(_user_payload(request.user))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout(request):
    Token.objects.filter(user=request.user).delete()
    return Response({"status": "logged_out"})