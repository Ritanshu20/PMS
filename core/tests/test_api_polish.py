import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

from core.testing import ProjectWithRolesMixin
from tasks.models import Task


class OpenApiTests(TestCase):
    def test_schema_generates_without_warnings_and_validates(self):
        """Every endpoint is documented; drf-spectacular finds nothing to guess."""
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "schema.yml"
            call_command(
                "spectacular",
                "--validate",
                "--fail-on-warn",
                "--file",
                str(target),
                stdout=StringIO(),
                stderr=StringIO(),
            )
            schema = target.read_text(encoding="utf-8")
        self.assertIn("/api/projects/{project_id}/tasks/{number}/", schema)
        self.assertIn("version_conflict", schema)

    def test_swagger_ui_and_schema_are_served(self):
        """/api/docs/ (Swagger UI) and /api/schema/ respond."""
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)
        self.assertEqual(self.client.get("/api/schema/").status_code, 200)


class ErrorFormatTests(ProjectWithRolesMixin, TestCase):
    """Every error has the same JSON shape: {"code": ..., "detail": ...}."""

    def setUp(self) -> None:
        super().setUp()
        Task.objects.create(project=self.project, number=1, title="t", reporter=self.owner)
        self.task_url = f"/api/projects/{self.project.pk}/tasks/1/"

    def assert_error(self, response, status_code: int, code: str):
        self.assertEqual(response.status_code, status_code, response.content)
        body = response.json()
        self.assertEqual(body["code"], code)
        self.assertIn("detail", body)
        return body

    def test_401_not_authenticated(self):
        """No token -> 401 not_authenticated."""
        self.assert_error(self.client_for(None).get(self.task_url), 401, "not_authenticated")

    def test_403_permission_denied_keeps_the_reason(self):
        """Viewer editing -> 403 with a human-readable reason."""
        body = self.assert_error(
            self.client_for(self.viewer).patch(self.task_url, {"version": 1, "title": "x"}),
            403,
            "permission_denied",
        )
        self.assertIn("role", body["detail"])

    def test_404_not_found(self):
        """Outsider -> 404 not_found (same as a missing project)."""
        self.assert_error(self.client_for(self.outsider).get(self.task_url), 404, "not_found")

    def test_400_serializer_validation_lists_fields(self):
        """Bad input -> 400 invalid with per-field messages."""
        body = self.assert_error(
            self.client_for(self.member).patch(self.task_url, {"title": "no version"}),
            400,
            "invalid",
        )
        self.assertIn("version", body["detail"])

    def test_400_business_rule_has_specific_code(self):
        """Workflow violations carry their own code."""
        response = self.client_for(self.member).post(
            f"{self.task_url}move/", {"version": 1, "status": "done", "position": 0}
        )
        self.assert_error(response, 400, "invalid_transition")

    def test_409_version_conflict_includes_current_data(self):
        """Stale version -> 409 with details.current."""
        body = self.assert_error(
            self.client_for(self.member).patch(self.task_url, {"version": 5, "title": "x"}),
            409,
            "version_conflict",
        )
        self.assertEqual(body["details"]["current"]["version"], 1)

    def test_405_method_not_allowed(self):
        """Unsupported verbs use the same format."""
        response = self.client_for(self.member).put(self.task_url, {})
        self.assert_error(response, 405, "method_not_allowed")

    def test_401_invalid_token(self):
        """A garbage Bearer token -> 401 token_not_valid."""
        from rest_framework.test import APIClient

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        self.assert_error(client.get(self.task_url), 401, "token_not_valid")


class ThrottleTests(ProjectWithRolesMixin, TestCase):
    def test_authenticated_requests_are_throttled_with_error_format(self):
        """With a 3/min user limit the 4th request is 429 throttled."""
        client = self.client_for(self.member)
        rates = {"user": "3/min", "anon": "100/min", "auth": "100/min"}
        with mock.patch.object(UserRateThrottle, "THROTTLE_RATES", rates):
            codes = [client.get("/api/projects/").status_code for _ in range(4)]
            last = client.get("/api/projects/")
        self.assertEqual(codes, [200, 200, 200, 429])
        self.assertEqual(last.json()["code"], "throttled")

    def test_health_endpoint_is_never_throttled(self):
        """Monitoring must always reach /api/health/."""
        rates = {"anon": "1/min", "user": "1/min", "auth": "1/min"}
        with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
            codes = [self.client.get("/api/health/").status_code for _ in range(3)]
            public_codes = [self.client.post("/api/auth/register/").status_code for _ in range(2)]
        self.assertEqual(codes, [200, 200, 200])
        self.assertEqual(public_codes, [400, 429], "the limit itself is active elsewhere")
