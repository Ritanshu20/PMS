from datetime import UTC, date, datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from activity.models import Notification
from activity.services import create_due_soon_notifications
from core.testing import ProjectWithRolesMixin, make_project
from projects.models import Project, ProjectMember
from tasks.models import Task

# 10 Mar 2026 15:00 IST. "Within 24 hours" covers due dates 10 Mar and 11 Mar.
NOW = datetime(2026, 3, 10, 9, 30, tzinfo=UTC)


class DueSoonTests(ProjectWithRolesMixin, TestCase):
    def make_task(self, number: int, due: date | None, status="todo", assignee="member"):
        return Task.objects.create(
            project=self.project,
            number=number,
            title=f"T{number}",
            due_date=due,
            status=status,
            assignee=self.member if assignee == "member" else None,
            reporter=self.owner,
            completed_at=NOW if status == "done" else None,
        )

    def run_job(self, now=NOW) -> int:
        with mock.patch("core.clock.now", return_value=now):
            return create_due_soon_notifications()

    def test_boundaries_today_and_tomorrow_only(self):
        """Due yesterday/day-after-tomorrow are not 'due soon'; today and tomorrow are."""
        self.make_task(1, date(2026, 3, 9))
        self.make_task(2, date(2026, 3, 10))
        self.make_task(3, date(2026, 3, 11))
        self.make_task(4, date(2026, 3, 12))
        self.assertEqual(self.run_job(), 2)
        numbers = sorted(Notification.objects.values_list("task__number", flat=True))
        self.assertEqual(numbers, [2, 3])

    def test_done_unassigned_and_archived_tasks_are_skipped(self):
        """Only open, assigned tasks in active projects are reminded."""
        self.make_task(1, date(2026, 3, 10), status="done")
        self.make_task(2, date(2026, 3, 10), assignee=None)
        archived = make_project(self.owner, key="OLD")
        archived.status = Project.Status.ARCHIVED
        archived.save()
        ProjectMember.objects.create(project=archived, user=self.member, role="member")
        Task.objects.create(
            project=archived,
            number=1,
            title="x",
            due_date=date(2026, 3, 10),
            assignee=self.member,
            reporter=self.owner,
        )
        self.assertEqual(self.run_job(), 0)

    def test_running_twice_creates_no_duplicates(self):
        """Idempotent: the second run on the same day creates nothing."""
        self.make_task(1, date(2026, 3, 11))
        self.assertEqual(self.run_job(), 1)
        self.assertEqual(self.run_job(), 0)
        self.assertEqual(Notification.objects.count(), 1)

    def test_next_day_creates_a_new_reminder(self):
        """At most one per task per user per DAY, so tomorrow reminds again."""
        self.make_task(1, date(2026, 3, 11))
        self.run_job()
        self.assertEqual(self.run_job(datetime(2026, 3, 11, 3, 0, tzinfo=UTC)), 1)

    def test_management_command_reports_count(self):
        """manage.py notify_due_tasks prints how many reminders it created."""
        self.make_task(1, date(2026, 3, 10))
        out = StringIO()
        with mock.patch("core.clock.now", return_value=NOW):
            call_command("notify_due_tasks", stdout=out)
        self.assertIn("Created 1", out.getvalue())


class NotificationApiTests(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        for index in range(3):
            Notification.objects.create(
                user=self.member, project=self.project, type="assigned", message=f"n{index}"
            )
        self.client = self.client_for(self.member)

    def test_list_and_unread_count(self):
        """A user sees only their own notifications."""
        Notification.objects.create(user=self.owner, project=self.project, type="assigned")
        response = self.client.get("/api/notifications/")
        self.assertEqual(response.json()["count"], 3)
        self.assertEqual(self.client.get("/api/notifications/unread-count/").json(), {"unread": 3})

    def test_mark_one_read_and_all_read(self):
        """Mark-read is per item or all at once."""
        first = Notification.objects.filter(user=self.member).first()
        response = self.client.post(f"/api/notifications/{first.pk}/read/")
        self.assertTrue(response.json()["is_read"])
        unread = self.client.get("/api/notifications/?unread=true").json()["count"]
        self.assertEqual(unread, 2)
        self.assertEqual(
            self.client.post("/api/notifications/read-all/").json(), {"marked_read": 2}
        )

    def test_cannot_read_someone_elses_notification(self):
        """IDOR: another user's notification id is 404."""
        other = Notification.objects.create(user=self.owner, project=self.project, type="assigned")
        self.assertEqual(self.client.post(f"/api/notifications/{other.pk}/read/").status_code, 404)

    def test_removed_member_no_longer_sees_project_notifications(self):
        """Notifications follow current membership."""
        ProjectMember.objects.filter(user=self.member).delete()
        self.assertEqual(self.client.get("/api/notifications/").json()["count"], 0)

    def test_anonymous_gets_401(self):
        """Notifications require authentication."""
        self.assertEqual(self.client_for(None).get("/api/notifications/").status_code, 401)
