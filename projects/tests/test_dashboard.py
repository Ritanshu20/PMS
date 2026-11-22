from datetime import UTC, date, datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from activity.models import ActivityLog
from core.testing import ProjectWithRolesMixin, make_project
from projects.models import ProjectMember
from sprints.models import Sprint
from tasks.models import Task

NOW = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)  # 10 Mar 2026 IST


class DashboardTests(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch("core.clock.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sprint = Sprint.objects.create(
            project=self.project,
            name="S1",
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 13),
            status="active",
            committed_points=20,
        )
        specs = [
            # status, points, due, in sprint
            ("todo", 5, date(2026, 3, 9), True),
            ("done", 5, date(2026, 3, 1), True),
            ("in_progress", 10, None, True),
            ("backlog", None, date(2026, 3, 20), False),
        ]
        for number, (status, points, due, in_sprint) in enumerate(specs, start=1):
            Task.objects.create(
                project=self.project,
                number=number,
                title=f"T{number}",
                status=status,
                story_points=points,
                due_date=due,
                sprint=self.sprint if in_sprint else None,
                assignee=self.member,
                reporter=self.owner,
                completed_at=NOW if status == "done" else None,
            )
        self.url = f"/api/projects/{self.project.pk}/dashboard/"

    def test_dashboard_numbers_match_controlled_data(self):
        """Counts per status, 1 overdue (done task not counted), sprint 5/20 = 25%."""
        data = self.client_for(self.viewer).get(self.url).json()
        self.assertEqual(
            data["tasks_by_status"],
            {"backlog": 1, "todo": 1, "in_progress": 1, "in_review": 0, "done": 1},
        )
        self.assertEqual(data["overdue"], 1)
        sprint = data["current_sprint"]
        self.assertEqual((sprint["done_points"], sprint["committed_points"]), (5, 20))
        self.assertEqual(sprint["progress_percent"], 25.0)
        self.assertEqual(sprint["days_left"], 3)
        self.assertEqual(data["average_velocity"], 0)
        meera = next(row for row in data["workload"] if row["username"] == "meera")
        self.assertEqual((meera["open_tasks"], meera["open_points"]), (3, 15))

    def test_no_active_sprint_gives_null_progress(self):
        """Without an active sprint there is nothing to show (and no division)."""
        Sprint.objects.update(status="completed")
        data = self.client_for(self.viewer).get(self.url).json()
        self.assertIsNone(data["current_sprint"])

    def test_dashboard_is_404_for_outsiders(self):
        """Role scoping: only members see the dashboard."""
        self.assertEqual(self.client_for(self.outsider).get(self.url).status_code, 404)

    def test_dashboard_query_count_is_constant(self):
        """No N+1: more tasks do not mean more queries."""
        client = self.client_for(self.viewer)
        with CaptureQueriesContext(connection) as before:
            client.get(self.url)
        for number in range(5, 25):
            Task.objects.create(
                project=self.project,
                number=number,
                title="x",
                sprint=self.sprint,
                assignee=self.manager,
                reporter=self.owner,
            )
        with CaptureQueriesContext(connection) as after:
            client.get(self.url)
        self.assertEqual(len(before), len(after))


class MyTasksTests(ProjectWithRolesMixin, TestCase):
    def test_only_my_open_tasks_from_my_projects(self):
        """Done tasks, other people's tasks and left projects are excluded."""
        other = make_project(self.owner, key="APP")
        ProjectMember.objects.create(project=other, user=self.member, role="member")
        left = make_project(self.owner, key="OLD")
        rows = [
            (self.project, 1, "todo", self.member, date(2026, 3, 5)),
            (self.project, 2, "done", self.member, None),
            (self.project, 3, "todo", self.manager, None),
            (other, 1, "in_progress", self.member, None),
            (left, 1, "todo", self.member, None),
        ]
        for project, number, status, assignee, due in rows:
            Task.objects.create(
                project=project,
                number=number,
                title="t",
                status=status,
                assignee=assignee,
                due_date=due,
                reporter=self.owner,
                completed_at=NOW if status == "done" else None,
            )
        response = self.client_for(self.member).get("/api/my-tasks/")
        self.assertEqual([t["key"] for t in response.json()["results"]], ["WEB-1", "APP-1"])
        with_done = self.client_for(self.member).get("/api/my-tasks/?include_done=true")
        self.assertEqual(with_done.json()["count"], 3)

    def test_my_tasks_requires_login(self):
        """Anonymous users get 401."""
        self.assertEqual(self.client_for(None).get("/api/my-tasks/").status_code, 401)


class ActivityFeedTests(ProjectWithRolesMixin, TestCase):
    def test_feed_is_paginated_newest_first_and_member_only(self):
        """Every member reads the feed; outsiders get 404."""
        client = self.client_for(self.member)
        for index in range(3):
            client.post(f"/api/projects/{self.project.pk}/tasks/", {"title": f"t{index}"})
        url = f"/api/projects/{self.project.pk}/activity/?page_size=2"
        data = self.client_for(self.viewer).get(url).json()
        self.assertEqual(data["count"], ActivityLog.objects.filter(project=self.project).count())
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["results"][0]["changes"]["title"], "t2")
        self.assertEqual(data["results"][0]["actor"]["username"], "meera")
        self.assertEqual(self.client_for(self.outsider).get(url).status_code, 404)
