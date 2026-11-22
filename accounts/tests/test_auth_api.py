from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts.models import User
from core.testing import DEFAULT_PASSWORD, make_user


class AuthTestCase(TestCase):
    def setUp(self) -> None:
        cache.clear()  # throttle counters live in the cache
        self.client = APIClient()

    def register(self, **overrides):
        data = {"username": "asha", "email": "asha@example.com", "password": DEFAULT_PASSWORD}
        data.update(overrides)
        return self.client.post("/api/auth/register/", data)

    def login(self, username="asha", password=DEFAULT_PASSWORD):
        return self.client.post("/api/auth/login/", {"username": username, "password": password})


class RegisterTests(AuthTestCase):
    def test_register_creates_user_and_never_returns_password(self):
        """A valid registration returns 201 with public fields only."""
        response = self.register(display_name="Asha")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["username"], "asha")
        self.assertNotIn("password", response.json())
        self.assertTrue(User.objects.get(username="asha").check_password(DEFAULT_PASSWORD))

    def test_duplicate_email_is_rejected_ignoring_case(self):
        """Emails are unique case-insensitively because members are added by email."""
        make_user("existing", email="asha@example.com")
        response = self.register(email="ASHA@example.com")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["detail"])

    def test_duplicate_username_is_rejected(self):
        """Usernames are used for @mentions, so they must be unique."""
        make_user("asha", email="other@example.com")
        response = self.register()
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.json()["detail"])

    def test_weak_password_is_rejected_by_django_validators(self):
        """Django's password validators run on registration."""
        response = self.register(password="12345")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json()["detail"])


class LoginAndTokenTests(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        make_user("asha")

    def test_login_returns_access_and_refresh_tokens(self):
        """Correct credentials give a JWT pair."""
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())
        self.assertIn("refresh", response.json())

    def test_login_with_wrong_password_returns_401(self):
        """Wrong credentials never give tokens."""
        self.assertEqual(self.login(password="wrong-password").status_code, 401)

    def test_protected_endpoint_without_token_returns_401(self):
        """/api/auth/me/ requires a valid access token."""
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_me_returns_current_user_with_valid_token(self):
        """A Bearer access token identifies the user."""
        access = self.login().json()["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "asha")

    def test_refresh_returns_new_access_token(self):
        """A refresh token can be exchanged for a new access token."""
        refresh = self.login().json()["refresh"]
        response = self.client.post("/api/auth/refresh/", {"refresh": refresh})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())

    def test_logout_blacklists_refresh_token(self):
        """After logout the same refresh token is refused."""
        tokens = self.login().json()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        logout = self.client.post("/api/auth/logout/", {"refresh": tokens["refresh"]})
        self.assertEqual(logout.status_code, 204)
        reuse = self.client.post("/api/auth/refresh/", {"refresh": tokens["refresh"]})
        self.assertEqual(reuse.status_code, 401)

    def test_logout_with_invalid_token_returns_400(self):
        """Garbage tokens give a clear error instead of a crash."""
        access = self.login().json()["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.post("/api/auth/logout/", {"refresh": "not-a-token"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_token")


class ThrottleTests(AuthTestCase):
    def test_login_is_throttled_after_rate_limit(self):
        """Brute-force protection: the 4th login in a minute gets 429 when the limit is 3."""
        make_user("asha")
        with mock.patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"auth": "3/min"}):
            codes = [self.login(password="wrong").status_code for _ in range(4)]
        self.assertEqual(codes[:3], [401, 401, 401])
        self.assertEqual(codes[3], 429)
