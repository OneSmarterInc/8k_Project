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


class ExpiringTokenAuthentication(TokenAuthentication):
    """TokenAuthentication that also rejects tokens older than the TTL."""

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)

        if is_token_expired(token):
            token.delete()
            raise AuthenticationFailed("Token has expired.")

        return user, token