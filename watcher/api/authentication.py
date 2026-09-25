"""
Security review #3: login tokens expire.

A token is valid for TOKEN_TTL_HOURS (default 12) after it was issued.
After that, any request using it gets 401 "Token has expired." and the
token is deleted; the React app then sends the user to the login page
(FE-001). Logging in again issues a fresh token.

Uses DRF's existing Token table (Token.created), so no migration.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed


def token_ttl():
    hours = getattr(settings, "TOKEN_TTL_HOURS", 12)
    return timedelta(hours=max(1, int(hours)))


def is_token_expired(token):
    return timezone.now() >= token.created + token_ttl()


def get_valid_token(user):
    """
    Return the user's current token, replacing it if it has expired.

    Called at login. A still-valid token is reused, so logging in on a
    second browser does not log the first one out.
    """
    token, created = Token.objects.get_or_create(user=user)

    if not created and is_token_expired(token):
        token.delete()
        token = Token.objects.create(user=user)

    return token


def auth_cookie_name():
    return getattr(settings, "AUTH_COOKIE_NAME", "watcher_auth")


def set_auth_cookie(response, token):
    """I-07: httponly=True is the point - JavaScript cannot read it."""
    response.set_cookie(
        auth_cookie_name(),
        token.key,
        max_age=int(token_ttl().total_seconds()),
        httponly=True,
        samesite=getattr(settings, "AUTH_COOKIE_SAMESITE", "Lax"),
        secure=bool(getattr(settings, "AUTH_COOKIE_SECURE", False)),
        path="/",
    )

    return response


def clear_auth_cookie(response):
    response.delete_cookie(
        auth_cookie_name(),
        path="/",
        samesite=getattr(settings, "AUTH_COOKIE_SAMESITE", "Lax"),
    )

    return response


class ExpiringTokenAuthentication(TokenAuthentication):
    """TokenAuthentication that also rejects tokens older than the TTL."""

    def authenticate(self, request):
        """
        I-07: cookie first, Authorization header as fallback.

        The header path is deliberately retained. Dropping it would log
        out every session in flight during a deploy and break any
        script or test that authenticates by header.
        """
        key = request.COOKIES.get(auth_cookie_name())

        if key:
            try:
                return self.authenticate_credentials(key)

            except AuthenticationFailed:
                # P-06: the cookie is stale, revoked or expired.
                #
                # Falling through rather than raising matters: the
                # request may ALSO carry a valid Authorization header,
                # and 401-ing it would defeat the exact reason the
                # header fallback exists - a browser that logged in
                # before a token rotation, or a script running on the
                # same domain as a logged-in session.
                #
                # Only AuthenticationFailed is caught. Anything else
                # (a database error, a bug) still propagates, so a real
                # failure is never silently turned into an anonymous
                # request.
                pass

        return super().authenticate(request)

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)

        if is_token_expired(token):
            token.delete()
            raise AuthenticationFailed("Token has expired.")

        return user, token