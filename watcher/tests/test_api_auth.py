"""W-010: every API endpoint requires a token; admin actions require staff."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient


class ApiAuthenticationTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="admin1", password="pw-admin-123", is_staff=True
        )
        self.user = User.objects.create_user(
            username="user1", password="pw-user-123", is_staff=False
        )
        self.client = APIClient()

    def test_anonymous_requests_are_rejected(self):
        endpoints = [
            ("get", "/api/filings/"),
            ("get", "/api/runs/"),
            ("get", "/api/runs/logs/"),
            ("post", "/api/runs/trigger/"),
            ("get", "/api/settings/smtp/"),
            ("post", "/api/settings/smtp/?action=save"),
            ("get", "/api/settings/schedule/"),
            ("post", "/api/settings/schedule/"),
            ("post", "/api/filings/1/resolve-amendment/"),
            ("post", "/api/knowledge-base/ask/"),
        ]
        for method, url in endpoints:
            with self.subTest(url=url, method=method):
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, 401)

    def test_normal_user_can_read(self):
        self.client.force_authenticate(self.user)
        for url in ("/api/filings/", "/api/runs/", "/api/settings/schedule/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_normal_user_cannot_run_admin_actions(self):
        self.client.force_authenticate(self.user)
        for method, url in (
            ("post", "/api/runs/trigger/"),
            ("get", "/api/settings/smtp/"),
            ("post", "/api/settings/smtp/?action=save"),
            ("post", "/api/settings/schedule/"),
        ):
            with self.subTest(url=url, method=method):
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, 403)

    @patch("watcher.api.views.SubprocessWatcherLauncher.launch", return_value=True)
    def test_admin_can_trigger_run(self, mock_launch):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/runs/trigger/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "started")
        mock_launch.assert_called_once()

    def test_login_returns_token_and_role(self):
        response = self.client.post(
            "/api/auth/login/",
            {"username": "admin1", "password": "pw-admin-123"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["is_staff"])
        self.assertEqual(body["token"], Token.objects.get(user=self.admin).key)

    def test_login_rejects_bad_password(self):
        response = self.client.post(
            "/api/auth/login/",
            {"username": "admin1", "password": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("token", response.json())

    def test_token_header_authenticates(self):
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        me = self.client.get("/api/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json(), {"username": "user1", "is_staff": False})

    def test_logout_invalidates_token(self):
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_knowledge_base_reaches_view_when_authenticated(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/knowledge-base/ask/", "not-json",
            content_type="application/json",
        )
        # Past authentication: the view itself rejects the bad body.
        self.assertIn(response.status_code, (400, 415))


class DebugSettingTests(TestCase):

    def test_debug_is_off_by_default(self):
        import os
        from django.conf import settings
        from config import settings as project_settings

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DJANGO_DEBUG", None)
            self.assertFalse(project_settings._env_bool("DJANGO_DEBUG", False))
        with patch.dict(os.environ, {"DJANGO_DEBUG": "True"}):
            self.assertTrue(project_settings._env_bool("DJANGO_DEBUG", False))
        self.assertIn("127.0.0.1", settings.ALLOWED_HOSTS)