"""Security review #3: login tokens expire after TOKEN_TTL_HOURS."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from watcher.api.authentication import get_valid_token, token_ttl


class TokenExpiryTests(TestCase):

    def setUp(self):
        # Keep login throttle counters (security review #2) out of the way.
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="expiry-user", password="pw-expiry-123"
        )
        self.client = APIClient()

    def _age_token(self, token, hours):
        Token.objects.filter(pk=token.pk).update(
            created=timezone.now() - timedelta(hours=hours)
        )

    def _use(self, key):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {key}")
        return self.client.get("/api/auth/me/")

    def _login(self):
        self.client.credentials()
        return self.client.post(
            "/api/auth/login/",
            {"username": "expiry-user", "password": "pw-expiry-123"},
            format="json",
        )

    def test_fresh_token_is_accepted(self):
        token = Token.objects.create(user=self.user)
        self.assertEqual(self._use(token.key).status_code, 200)

    @override_settings(TOKEN_TTL_HOURS=12)
    def test_expired_token_is_rejected_and_deleted(self):
        token = Token.objects.create(user=self.user)
        self._age_token(token, 13)
        response = self._use(token.key)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Token has expired.")
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())

    @override_settings(TOKEN_TTL_HOURS=12)
    def test_token_just_inside_ttl_still_works(self):
        token = Token.objects.create(user=self.user)
        self._age_token(token, 11)
        self.assertEqual(self._use(token.key).status_code, 200)

    @override_settings(TOKEN_TTL_HOURS=12)
    def test_login_replaces_an_expired_token(self):
        old = Token.objects.create(user=self.user)
        self._age_token(old, 13)
        response = self._login()
        self.assertEqual(response.status_code, 200)
        new_key = response.json()["token"]
        self.assertNotEqual(new_key, old.key)
        self.assertEqual(self._use(new_key).status_code, 200)

    def test_login_reuses_a_valid_token(self):
        # A second browser logging in must not log the first one out.
        first = self._login().json()["token"]
        second = self._login().json()["token"]
        self.assertEqual(first, second)
        self.assertEqual(self._use(first).status_code, 200)

    @override_settings(TOKEN_TTL_HOURS=12)
    def test_login_works_even_with_an_expired_token_header(self):
        old = Token.objects.create(user=self.user)
        self._age_token(old, 13)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {old.key}")
        response = self.client.post(
            "/api/auth/login/",
            {"username": "expiry-user", "password": "pw-expiry-123"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(TOKEN_TTL_HOURS=1)
    def test_custom_ttl_is_respected(self):
        token = Token.objects.create(user=self.user)
        self._age_token(token, 2)
        self.assertEqual(self._use(token.key).status_code, 401)

    @override_settings(TOKEN_TTL_HOURS=0)
    def test_zero_or_negative_ttl_falls_back_to_one_hour(self):
        self.assertEqual(token_ttl(), timedelta(hours=1))

    def test_get_valid_token_keeps_a_valid_token(self):
        token = Token.objects.create(user=self.user)
        self.assertEqual(get_valid_token(self.user).key, token.key)

    def test_logout_still_works(self):
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 200)
        self.assertEqual(self._use(token.key).status_code, 401)