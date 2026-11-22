"""
Phase 14 pages: planning, sprint/burndown, members, notifications, reports,
dashboard, my tasks and the custom error pages. Session-login browser tests.
"""

from datetime import date, timedelta

from django.db import connection
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from activity.models import Notification
from core import clock
from core.testing import ProjectWithRolesMixin
from projects.models import Project, ProjectMember
from sprints.models import Sprint
from tasks.models import Task, TimeEntry


class PageTestCase(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        for number, (title, points) in enumerate((("Login", 3), ("Signup", 5)), start=1):
            Task.objects.create(
                project=self.project,
                number=number,
                title=title,
                story_points=points,
                status="todo",
                reporter=self.owner,
                assignee=self.member,
            )
        Project.objects.filter(pk=self.project.pk).update(task_counter=2)
        self.base = f"/projects/{self.project.pk}"

    def browser(self, user) -> Client:
        client = Client()
        client.force_login(user)
        return client


class PlanningFlowTests(PageTestCase):
    def test_full_sprint_flow_through_the_pages(self):
        """Create sprint -> plan tasks -> start -> burndown shown -> complete to backlog."""
        manager = self.browser(self.manager)
        today = clock.today()
        manager.post(
            f"{self.base}/sprints/new/",
            {
                "name": "Sprint 1",
                "start_date": today - timedelta(days=1),
                "end_date": today + timedelta(days=5),
            },
        )
        sprint = Sprint.objects.get(name="Sprint 1")
        for number in (1, 2):
            manager.post(
                f"{self.base}/backlog/plan/", {"sprint_id": sprint.pk, "task_number": number}
            )
        self.assertEqual(Task.objects.filter(sprint=sprint).count(), 2)
        response = manager.post(f"{self.base}/sprints/{sprint.pk}/start/")
        self.assertRedirects(response, f"{self.base}/sprints/{sprint.pk}/")
        page = manager.get(f"{self.base}/sprints/{sprint.pk}/")
        self.assertContains(page, 'id="burndown-data"')
        self.assertContains(page, "Committed 8 pts")
        Task.objects.filter(number=1).update(status="done", completed_at=clock.now())
        manager.post(f"{self.base}/sprints/{sprint.pk}/complete/", {"unfinished_action": "backlog"})
        sprint.refresh_from_db()
        self.assertEqual((sprint.status, sprint.completed_points), ("completed", 3))
        self.assertIsNone(Task.objects.get(number=2).sprint_id)

    def test_complete_to_sprint_without_target_shows_message(self):
        """The unfinished-task choice must be complete."""
        sprint = Sprint.objects.create(
            project=self.project,
            name="S",
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 6),
            status="active",
        )
        response = self.browser(self.manager).post(
            f"{self.base}/sprints/{sprint.pk}/complete/",
            {"unfinished_action": "sprint"},
            follow=True,
        )
        self.assertContains(response, "Choose the sprint for unfinished tasks")
        sprint.refresh_from_db()
        self.assertEqual(sprint.status, "active")

    def test_backlog_page_by_role(self):
        """Managers get planning controls, viewers only read, outsiders get 404."""
        Sprint.objects.create(
            project=self.project, name="S", start_date=date(2026, 3, 2), end_date=date(2026, 3, 6)
        )
        manager_page = self.browser(self.manager).get(f"{self.base}/backlog/")
        self.assertContains(manager_page, "Start sprint")
        self.assertContains(manager_page, ">Plan<")
        viewer_page = self.browser(self.viewer).get(f"{self.base}/backlog/")
        self.assertEqual(viewer_page.status_code, 200)
        self.assertNotContains(viewer_page, "Start sprint")
        self.assertEqual(self.browser(self.outsider).get(f"{self.base}/backlog/").status_code, 404)

    def test_member_cannot_create_sprint_from_page(self):
        """The page action uses the same permission module (403)."""
        response = self.browser(self.member).post(
            f"{self.base}/sprints/new/",
            {"name": "x", "start_date": "2026-03-02", "end_date": "2026-03-06"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Sprint.objects.exists())

    def test_backlog_page_query_count_is_constant(self):
        """No N+1 on the planning page."""
        sprint = Sprint.objects.create(
            project=self.project, name="S", start_date=date(2026, 3, 2), end_date=date(2026, 3, 6)
        )
        # One task in the sprint and one in the backlog, so every prefetch already runs.
        Task.objects.filter(number=1).update(sprint=sprint)
        client = self.browser(self.manager)
        with CaptureQueriesContext(connection) as few:
            client.get(f"{self.base}/backlog/")
        for number in range(3, 15):
            Task.objects.create(
                project=self.project,
                number=number,
                title="t",
                reporter=self.owner,
                sprint=sprint if number % 2 else None,
            )
        with self.assertNumQueries(len(few)):
            client.get(f"{self.base}/backlog/")


class MemberPageTests(PageTestCase):
    def test_manager_adds_member_and_changes_role(self):
        """Members page: add by email, change role."""
        client = self.browser(self.manager)
        client.post(f"{self.base}/members/add/", {"email": self.outsider.email, "role": "viewer"})
        member = ProjectMember.objects.get(user=self.outsider)
        client.post(f"{self.base}/members/{member.pk}/role/", {"role": "member"})
        member.refresh_from_db()
        self.assertEqual(member.role, "member")

    def test_manager_cannot_demote_owner_and_viewer_cannot_manage(self):
        """Owners are out of a manager's reach; viewers manage nobody."""
        owner_member = ProjectMember.objects.get(user=self.owner)
        response = self.browser(self.manager).post(
            f"{self.base}/members/{owner_member.pk}/role/", {"role": "member"}
        )
        self.assertEqual(response.status_code, 403)
        viewer = self.browser(self.viewer).post(
            f"{self.base}/members/add/", {"email": self.outsider.email, "role": "member"}
        )
        self.assertEqual(viewer.status_code, 403)

    def test_last_owner_message(self):
        """The last-owner rule shows a clear message."""
        owner_member = ProjectMember.objects.get(user=self.owner)
        response = self.browser(self.owner).post(
            f"{self.base}/members/{owner_member.pk}/role/", {"role": "manager"}, follow=True
        )
        self.assertContains(response, "at least one owner")

    def test_members_page_controls_by_role(self):
        """Only managers/owners see role selects; only owners see archive."""
        owner_page = self.browser(self.owner).get(f"{self.base}/members/")
        self.assertContains(owner_page, "Archive project")
        manager_page = self.browser(self.manager).get(f"{self.base}/members/")
        self.assertContains(manager_page, "Add member")
        self.assertNotContains(manager_page, "Archive project")
        viewer_page = self.browser(self.viewer).get(f"{self.base}/members/")
        self.assertNotContains(viewer_page, "Add member")


class NotificationPageTests(PageTestCase):
    def test_badge_count_and_mark_all_read(self):
        """The navbar badge shows unread count and disappears after mark-all-read."""
        for index in range(2):
            Notification.objects.create(
                user=self.member,
                project=self.project,
                type="assigned",
                message=f"n{index}",
                link="/projects/",
            )
        client = self.browser(self.member)
        page = client.get("/my-tasks/")
        self.assertContains(page, 'id="unread-badge">2<')
        client.post("/notifications/read-all/")
        self.assertNotContains(client.get("/my-tasks/"), 'id="unread-badge"')

    def test_open_marks_read_and_follows_local_link_only(self):
        """Opening goes to the task; non-local links are never followed (no open redirect)."""
        good = Notification.objects.create(
            user=self.member, project=self.project, type="assigned", link=f"{self.base}/tasks/1/"
        )
        bad = Notification.objects.create(
            user=self.member, project=self.project, type="assigned", link="//evil.example.com/"
        )
        client = self.browser(self.member)
        self.assertRedirects(
            client.post(f"/notifications/{good.pk}/open/"), f"{self.base}/tasks/1/"
        )
        good.refresh_from_db()
        self.assertIsNotNone(good.read_at)
        self.assertRedirects(client.post(f"/notifications/{bad.pk}/open/"), "/notifications/")

    def test_cannot_open_someone_elses_notification(self):
        """IDOR on the page action: 404."""
        other = Notification.objects.create(user=self.owner, project=self.project, type="assigned")
        self.assertEqual(
            self.browser(self.member).post(f"/notifications/{other.pk}/open/").status_code, 404
        )


class ReportAndDashboardPageTests(PageTestCase):
    def test_time_report_page_matches_selector_numbers(self):
        """The page shows exactly what report_selectors compute."""
        TimeEntry.objects.create(
            task_id=Task.objects.get(number=1).pk,
            user=self.member,
            minutes=95,
            date=date(2026, 3, 2),
        )
        Task.objects.filter(number=1).update(estimate_minutes=190)
        page = self.browser(self.viewer).get(f"{self.base}/reports/time/")
        self.assertContains(page, '<td class="text-end">95</td>', html=False)
        self.assertContains(page, "50.0%")
        self.assertContains(page, "95 min")

    def test_dashboard_page_shows_counts_and_overdue(self):
        """Dashboard numbers come from the dashboard selector."""
        Task.objects.filter(number=2).update(due_date=clock.today() - timedelta(days=1))
        page = self.browser(self.viewer).get(f"{self.base}/dashboard/")
        self.assertContains(page, "Overdue")
        self.assertContains(page, '<div class="fs-4 fw-semibold text-danger">1</div>', html=False)
        self.assertContains(page, "No active sprint")

    def test_my_tasks_page_lists_only_my_open_tasks(self):
        """My tasks: assigned to me, open, across my projects."""
        page = self.browser(self.member).get("/my-tasks/")
        self.assertContains(page, "WEB-1")
        self.assertContains(page, "WEB-2")
        empty = self.browser(self.viewer).get("/my-tasks/")
        self.assertContains(empty, "Nothing assigned to you")

    def test_activity_page_is_paginated(self):
        """The activity feed page uses the pagination partial."""
        client = self.browser(self.member)
        for index in range(25):
            client.post(
                f"{self.base}/tasks/new/",
                {"title": f"t{index}", "priority": "low", "status": "backlog"},
            )
        page = client.get(f"{self.base}/activity/")
        self.assertContains(page, "1 / 2")


class ErrorPageTests(PageTestCase):
    def test_custom_404_page(self):
        """Non-members see the friendly 404 page (same as a missing page)."""
        response = self.browser(self.outsider).get(f"{self.base}/board/")
        self.assertContains(response, "Page not found", status_code=404)

    def test_custom_403_page_shows_reason(self):
        """Forbidden actions explain the reason."""
        response = self.browser(self.viewer).get(f"{self.base}/tasks/new/")
        self.assertContains(response, "You can't do that", status_code=403)
        self.assertContains(response, "role does not allow", status_code=403)

    def test_custom_500_page_renders_without_context(self):
        """The 500 page is standalone so it works even when the database is down."""
        html = render_to_string("500.html")
        self.assertIn("Something went wrong", html)
