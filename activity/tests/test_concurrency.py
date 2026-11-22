from datetime import UTC, date, datetime
from unittest import mock

from django.test import TransactionTestCase, tag

from activity.models import Notification
from activity.services import create_due_soon_notifications
from core.testing import make_project, make_user, run_concurrently
from tasks.models import Task

NOW = datetime(2026, 3, 10, 9, 30, tzinfo=UTC)


@tag("concurrency")
class DueSoonConcurrencyTests(TransactionTestCase):
    """Concurrency test 6."""

    def test_concurrent_runs_create_no_duplicate_notifications(self):
        """Two simultaneous notify_due_tasks runs still give one reminder per task."""
        owner = make_user("owner")
        project = make_project(owner, key="RACE")
        for number in range(1, 6):
            Task.objects.create(
                project=project,
                number=number,
                title=f"T{number}",
                due_date=date(2026, 3, 11),
                assignee=owner,
                reporter=owner,
            )
        with mock.patch("core.clock.now", return_value=NOW):
            outcomes = run_concurrently([create_due_soon_notifications] * 3)
        self.assertTrue(all(kind == "ok" for kind, _ in outcomes), outcomes)
        self.assertEqual(sum(result for _, result in outcomes), 5)
        self.assertEqual(Notification.objects.filter(type="due_soon").count(), 5)
