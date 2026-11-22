"""
Phase 15 security checks that must keep passing:
deploy settings, headers, cookie flags, login throttling, XSS escaping,
no unsafe template/code patterns, and an IDOR sweep over every web page.
"""

import os
import re
import secrets
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import get_resolver

from core.testing import DEFAULT_PASSWORD, ProjectWithRolesMixin
from core.tests.test_full_suite import PARAM
from tasks.models import Task

BASE_DIR = Path(settings.BASE_DIR)
SOURCE_DIRS = ["accounts", "activity", "config", "core", "projects", "sprints", "tasks"]


def project_files(pattern: str):
    for folder in SOURCE_DIRS + ["templates"]:
        for path in (BASE_DIR / folder).rglob(pattern):
            if "migrations" not in path.parts and "tests" not in path.parts:
                yield path


class DeploySettingsTests(TestCase):
    def test_check_deploy_has_no_warnings_except_hsts_preload(self):
        """manage.py check --deploy with production env: only W021 (preload is a manual opt-in)."""
        env = dict(
            os.environ,
            DEBUG="False",
            SECRET_KEY=secrets.token_urlsafe(50),
            ALLOWED_HOSTS="pms.example.com",
            # Pin production values: CI sets SECURE_SSL_REDIRECT=False for its own http tests,
            # and os.environ (or a developer's .env) must not leak into this check.
            SECURE_SSL_REDIRECT="True",
            SECURE_HSTS_SECONDS="31536000",
            SECURE_HSTS_INCLUDE_SUBDOMAINS="True",
        )
        result = subprocess.run(  # noqa: S603 - fixed arguments, our own manage.py
            [sys.executable, "manage.py", "check", "--deploy"],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        warnings = set(re.findall(r"\((\w+\.\w\d+)\)", output))
        self.assertEqual(warnings - {"security.W021"}, set(), output)

    def test_jwt_lifetimes_are_short_and_refresh_tokens_rotate(self):
        """Access tokens live 15 minutes; used refresh tokens are blacklisted."""
        jwt = settings.SIMPLE_JWT
        self.assertLessEqual(jwt["ACCESS_TOKEN_LIFETIME"], timedelta(minutes=15))
        self.assertLessEqual(jwt["REFRESH_TOKEN_LIFETIME"], timedelta(days=1))
        self.assertTrue(jwt["ROTATE_REFRESH_TOKENS"])
        self.assertTrue(jwt["BLACKLIST_AFTER_ROTATION"])


class HeaderAndCookieTests(ProjectWithRolesMixin, TestCase):
    def test_security_headers_on_pages(self):
        """Clickjacking, MIME sniffing and referrer leaks are blocked."""
        response = self.client.get("/accounts/login/")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")

    def test_session_and_csrf_cookies_are_http_only(self):
        """JavaScript cannot read the session or CSRF cookies."""
        response = self.client.post(
            "/accounts/login/", {"username": "meera", "password": DEFAULT_PASSWORD}
        )
        self.assertTrue(response.cookies["sessionid"]["httponly"])
        self.assertTrue(response.cookies["csrftoken"]["httponly"])
        self.assertEqual(response.cookies["sessionid"]["samesite"], "Lax")


@override_settings(LOGIN_FAILURE_LIMIT=3)
class LoginThrottleTests(ProjectWithRolesMixin, TestCase):
    def login(self, password: str):
        return self.client.post("/accounts/login/", {"username": "meera", "password": password})

    def test_after_too_many_failures_even_the_right_password_is_refused(self):
        """3 failures -> the 4th attempt is 429 and does not log in."""
        for _ in range(3):
            self.assertEqual(self.login("wrong-password").status_code, 200)
        response = self.login(DEFAULT_PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many failed logins", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_successful_login_below_the_limit(self):
        """Two typos then the right password still works."""
        self.login("wrong-1")
        self.login("wrong-2")
        self.assertEqual(self.login(DEFAULT_PASSWORD).status_code, 302)


class XssTests(ProjectWithRolesMixin, TestCase):
    def test_task_title_with_html_is_escaped_on_board_and_detail(self):
        """User-provided text is always autoescaped."""
        Task.objects.create(
            project=self.project,
            number=1,
            title='<img src=x onerror="alert(1)">',
            reporter=self.owner,
        )
        client = Client()
        client.force_login(self.member)
        for url in (f"/projects/{self.project.pk}/board/", f"/projects/{self.project.pk}/tasks/1/"):
            with self.subTest(url=url):
                page = client.get(url)
                self.assertNotContains(page, "<img src=x")
                self.assertContains(page, "&lt;img src=x")

    def test_no_unsafe_output_patterns_in_templates_or_code(self):
        """No |safe, autoescape off, mark_safe, csrf_exempt or print() anywhere."""
        forbidden = re.compile(r"\|\s*safe\b|autoescape\s+off|mark_safe|csrf_exempt|\bprint\(")
        offenders = []
        for pattern in ("*.html", "*.py"):
            for path in project_files(pattern):
                if forbidden.search(path.read_text(encoding="utf-8")):
                    offenders.append(str(path.relative_to(BASE_DIR)))
        self.assertEqual(offenders, [])


class WebIdorSweepTests(ProjectWithRolesMixin, TestCase):
    def test_outsider_gets_404_on_every_project_web_page_and_action(self):
        """Every server-rendered URL under /projects/<id>/ is closed to non-members."""
        Task.objects.create(project=self.project, number=1, title="secret", reporter=self.owner)
        values = {
            name: 1
            for name in (
                "number",
                "sprint_id",
                "comment_id",
                "entry_id",
                "member_id",
                "blocker_number",
            )
        }
        values["project_id"] = self.project.pk
        client = Client()
        client.force_login(self.outsider)
        checked = 0
        for pattern in get_resolver().url_patterns:
            route = str(pattern.pattern)
            if not route.startswith("projects/"):
                continue
            for full_route, _, _ in _walk_any(pattern, ""):
                if "project_id" not in full_route:
                    continue
                url = "/" + PARAM.sub(lambda match: str(values[match.group(1)]), full_route)
                with self.subTest(url=url):
                    self.assertIn(client.get(url).status_code, (404, 405))
                    self.assertEqual(client.post(url, {"body": "x"}).status_code, 404)
                checked += 1
        self.assertGreater(checked, 25)
        self.assertEqual(Task.objects.get(number=1).title, "secret")


def _walk_any(entry, prefix: str):
    """Yield (route, callback, name) for every URL, including function views."""
    route = prefix + str(entry.pattern)
    if hasattr(entry, "url_patterns"):
        for child in entry.url_patterns:
            yield from _walk_any(child, route)
    else:
        yield route, entry.callback, entry.name
