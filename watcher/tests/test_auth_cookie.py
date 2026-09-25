"""I-07: the session token is delivered as an HttpOnly cookie.

Proves three things:

1. Login sets an HttpOnly cookie, so JavaScript cannot read the token.
2. That cookie authenticates subsequent requests.
3. The Authorization header STILL works, so nothing that authenticates
   by header was broken.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

LOGIN_URL = "/api/auth/login/"
LOGOUT_URL = "/api/auth/logout/"
ME_URL = "/api/auth/me/"

COOKIE = "watcher_auth"


class AuthCookieTests(TestCase):

    def setUp(self):
        # Every test here logs in, and the login throttle counts
        # 5/min per username and 10/min per IP in the shared cache.
        # Without this the later tests get 429 instead of 200, exactly
        # as test_login_throttle.py already guards against.
        cache.clear()
        self.addCleanup(cache.clear)

        User = get_user_model()
        self.user = User.objects.create_user(
            username="cookie-user",
            password="pw-cookie-user-123",
        )
        self.client = APIClient()

    def login(self):
        return self.client.post(
            LOGIN_URL,
            {"username": "cookie-user", "password": "pw-cookie-user-123"},
            format="json",
        )

    # ------------------------------------------------------------------
    # The cookie itself
    # ------------------------------------------------------------------

    def test_login_sets_an_httponly_cookie(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertIn(COOKIE, response.cookies)

        morsel = response.cookies[COOKIE]

        # The whole point: unreadable from JavaScript.
        self.assertTrue(morsel["httponly"])
        self.assertEqual(morsel["samesite"], "Lax")
        self.assertEqual(morsel["path"], "/")

    def test_cookie_value_is_the_token_and_expires_with_it(self):
        response = self.login()

        token = Token.objects.get(user=self.user)
        morsel = response.cookies[COOKIE]

        self.assertEqual(morsel.value, token.key)
        self.assertEqual(
            int(morsel["max-age"]),
            settings.TOKEN_TTL_HOURS * 3600,
        )

    def test_secure_flag_is_off_by_default_for_http_development(self):
        # A Secure cookie is not sent over http://, which would 401
        # every request after login on a plain-HTTP dev server. This is
        # the bug that broke the first attempt at this change.
        self.assertFalse(self.login().cookies[COOKIE]["secure"])

    @override_settings(AUTH_COOKIE_SECURE=True)
    def test_secure_flag_is_set_when_configured(self):
        # Production sets AUTH_COOKIE_SECURE=True so the cookie never
        # travels in clear over the network.
        self.assertTrue(self.login().cookies[COOKIE]["secure"])

    # ------------------------------------------------------------------
    # The cookie authenticates
    # ------------------------------------------------------------------

    def test_cookie_authenticates_without_any_header(self):
        self.login()

        # APIClient keeps cookies between requests, like a browser.
        response = self.client.get(ME_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "cookie-user")

    def test_expired_token_in_cookie_is_rejected(self):
        self.login()

        token = Token.objects.get(user=self.user)
        token.created = timezone.now() - timedelta(
            hours=settings.TOKEN_TTL_HOURS + 1
        )
        token.save(update_fields=["created"])

        self.assertEqual(self.client.get(ME_URL).status_code, 401)

    def test_logout_clears_the_cookie(self):
        self.login()

        response = self.client.post(LOGOUT_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies[COOKIE].value, "")
        self.assertEqual(self.client.get(ME_URL).status_code, 401)

    def test_garbage_cookie_is_rejected(self):
        self.client.cookies[COOKIE] = "not-a-real-token"

        self.assertEqual(self.client.get(ME_URL).status_code, 401)

    # ------------------------------------------------------------------
    # Regression: the header path must still work
    # ------------------------------------------------------------------

    def test_authorization_header_still_authenticates(self):
        # A session that started before this change, or any script
        # authenticating by header, must keep working.
        key = self.login().json()["token"]

        header_client = APIClient()
        header_client.credentials(HTTP_AUTHORIZATION=f"Token {key}")

        response = header_client.get(ME_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "cookie-user")

    def test_login_response_still_contains_the_token(self):
        self.assertIn("token", self.login().json())

    def test_no_cookie_and_no_header_is_rejected(self):
        self.assertEqual(APIClient().get(ME_URL).status_code, 401)