from datetime import UTC, date, datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from core import clock


class HealthEndpointTests(TestCase):
    def test_health_endpoint_returns_ok_without_authentication(self):
        """/api/health/ is public and confirms the database answers."""
        response = APIClient().get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})


class DatabaseTests(TestCase):
    def test_tests_run_on_postgresql(self):
        """The project must never silently fall back to another database."""
        self.assertEqual(connection.vendor, "postgresql")


class ClockTests(TestCase):
    def test_today_uses_project_timezone_not_utc(self):
        """20:00 UTC on Jan 1 is already Jan 2 in Asia/Kolkata (UTC+5:30)."""
        fixed = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)
        with mock.patch("core.clock.now", return_value=fixed):
            self.assertEqual(clock.today(), date(2026, 1, 2))
