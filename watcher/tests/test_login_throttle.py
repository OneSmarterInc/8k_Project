from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

LOGIN_URL = "/api/auth/login/"


class LoginThrottleTests(TestCase):
    """Brute-force protection on POST /api/auth/login/ only."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.user = User.objects.create_user("alice", password="correct-pw")

    def _login(self, username, password, ip="10.0.0.1"):
        return self.client.post(
            LOGIN_URL,
            {"username": username, "password": password},
            format="json",
            REMOTE_ADDR=ip,
        )

    def test_normal_login_still_works(self):
        response = self._login("alice", "correct-pw")
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())

    def test_wrong_password_still_returns_400_before_limit(self):
        response = self._login("alice", "wrong")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid username or password.")

    def test_per_username_limit(self):
        # Different IPs, same account: blocked after 5 attempts per minute.
        for i in range(5):
            self.assertEqual(self._login("alice", "wrong", ip=f"10.0.1.{i}").status_code, 400)

        blocked = self._login("alice", "correct-pw", ip="10.0.1.99")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("detail", blocked.json())

    def test_per_ip_limit(self):
        # Same IP, different usernames: blocked after 10 attempts per minute.
        for i in range(10):
            self.assertEqual(self._login(f"user{i}", "wrong").status_code, 400)

        self.assertEqual(self._login("alice", "correct-pw").status_code, 429)

    def test_other_ip_and_user_not_affected(self):
        for i in range(5):
            self._login("alice", "wrong", ip="10.0.2.1")

        other = self._login("bob", "whatever", ip="10.0.2.2")
        self.assertEqual(other.status_code, 400)  # not 429

    def test_other_endpoints_are_not_throttled(self):
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        for _ in range(30):
            self.assertEqual(self.client.get("/api/runs/logs/").status_code, 200)