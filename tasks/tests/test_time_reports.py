from datetime import UTC, date, datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.testing import ProjectWithRolesMixin, make_project
from projects.models import ProjectMember
from sprints.models import Sprint
from tasks.models import Task, TimeEntry

NOW = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)  # 10 Mar 2026 11:30 IST
TODAY = "2026-03-10"


class TimeTestCase(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch("core.clock.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.task = Task.objects.create(
            project=self.project, number=1, title="API", reporter=self.owner, estimate_minutes=120
        )
        self.base = f"/api/projects/{self.project.pk}"

    def log(self, minutes, day=TODAY, user=None, number=1):
        return self.client_for(user or self.member).post(
            f"{self.base}/tasks/{number}/time/", {"minutes": minutes, "date": day}
        )


class TimeEntryRuleTests(TimeTestCase):
    def test_valid_entry_is_logged_for_the_acting_user(self):
        """A member logs 90 minutes on a task."""
        response = self.log(90)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["user"]["username"], "meera")
        self.assertEqual(response.json()["minutes"], 90)

    def test_minutes_must_be_between_1_and_1440(self):
        """0 and 1441 are rejected; 1 and 1440 are accepted."""
        self.assertEqual(self.log(0).status_code, 400)
        self.assertEqual(self.log(1441).status_code, 400)
        self.assertEqual(self.log(1).status_code, 201)
        self.assertEqual(self.log(1440, day="2026-03-09").status_code, 201)

    def test_future_date_is_rejected(self):
        """Time is logged after the work, never in advance."""
        self.assertEqual(self.log(30, day="2026-03-11").status_code, 400)

    def test_daily_cap_boundary_across_projects(self):
        """600 + 840 = 1440 is fine; one more minute (even in another project) is not."""
        self.assertEqual(self.log(600).status_code, 201)
        self.assertEqual(self.log(840).status_code, 201)
        other = make_project(self.owner, key="OTH")
        ProjectMember.objects.create(project=other, user=self.member, role="member")
        Task.objects.create(project=other, number=1, title="x", reporter=self.owner)
        response = self.client_for(self.member).post(
            f"/api/projects/{other.pk}/tasks/1/time/", {"minutes": 1, "date": TODAY}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "daily_limit")

    def test_viewer_cannot_log_time(self):
        """Matrix: viewers cannot log time; outsiders get 404."""
        self.assertEqual(self.log(30, user=self.viewer).status_code, 403)
        self.assertEqual(self.log(30, user=self.outsider).status_code, 404)

    def test_users_edit_only_their_own_entries(self):
        """Even a manager cannot edit someone else's entry."""
        entry_id = self.log(30).json()["id"]
        url = f"{self.base}/time/{entry_id}/"
        self.assertEqual(self.client_for(self.manager).patch(url, {"minutes": 45}).status_code, 403)
        response = self.client_for(self.member).patch(url, {"minutes": 45})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["minutes"], 45)

    def test_edit_rechecks_daily_cap_without_counting_itself(self):
        """Raising 1000 -> 1440 is allowed (the old 1000 is not double counted)."""
        entry_id = self.log(1000).json()["id"]
        response = self.client_for(self.member).patch(
            f"{self.base}/time/{entry_id}/", {"minutes": 1440}
        )
        self.assertEqual(response.status_code, 200)

    def test_owner_of_entry_and_managers_can_delete_but_other_members_cannot(self):
        """Managers may delete any entry; other members only their own."""
        other_member = self.users_by_role["owner"]
        first = self.log(30).json()["id"]
        second = self.log(30).json()["id"]
        ProjectMember.objects.filter(user=other_member).update(role="member")
        self.assertEqual(
            self.client_for(other_member).delete(f"{self.base}/time/{first}/").status_code, 403
        )
        self.assertEqual(
            self.client_for(self.member).delete(f"{self.base}/time/{first}/").status_code, 204
        )
        self.assertEqual(
            self.client_for(self.manager).delete(f"{self.base}/time/{second}/").status_code, 204
        )

    def test_entry_of_another_project_is_404(self):
        """IDOR: entry ids are scoped to the URL's project."""
        other = make_project(self.outsider, key="OTH")
        task = Task.objects.create(project=other, number=1, title="x", reporter=self.outsider)
        entry = TimeEntry.objects.create(
            task=task, user=self.outsider, minutes=5, date=date(2026, 3, 1)
        )
        response = self.client_for(self.manager).delete(f"{self.base}/time/{entry.pk}/")
        self.assertEqual(response.status_code, 404)


class ReportTests(TimeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sprint = Sprint.objects.create(
            project=self.project,
            name="S1",
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 13),
            status="active",
        )
        Task.objects.filter(pk=self.task.pk).update(sprint=self.sprint)
        self.task_two = Task.objects.create(
            project=self.project, number=2, title="UI", reporter=self.owner
        )
        TimeEntry.objects.create(
            task=self.task, user=self.member, minutes=100, date=date(2026, 3, 9)
        )
        TimeEntry.objects.create(
            task=self.task, user=self.manager, minutes=50, date=date(2026, 3, 10)
        )
        TimeEntry.objects.create(
            task=self.task_two, user=self.member, minutes=30, date=date(2026, 3, 10)
        )

    def get(self, path: str):
        response = self.client_for(self.viewer).get(f"{self.base}/reports/{path}")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"]

    def test_time_by_task_with_estimate_comparison(self):
        """Task 1: estimate 120, logged 150 -> 125%; task 2 has no estimate -> None."""
        rows = self.get("time-by-task/")
        self.assertEqual(rows[0]["logged_minutes"], 150)
        self.assertEqual(rows[0]["percent_of_estimate"], 125.0)
        self.assertTrue(rows[0]["over_estimate"])
        self.assertEqual(rows[1]["logged_minutes"], 30)
        self.assertIsNone(rows[1]["percent_of_estimate"])
        self.assertFalse(rows[1]["over_estimate"])

    def test_time_by_task_filtered_by_sprint(self):
        """?sprint= limits the report to that sprint's tasks."""
        rows = self.get(f"time-by-task/?sprint={self.sprint.pk}")
        self.assertEqual([row["number"] for row in rows], [1])

    def test_time_by_user_with_date_range(self):
        """meera 130, manoj 50 overall; only 10 Mar -> manoj 50, meera 30."""
        rows = self.get("time-by-user/")
        self.assertEqual(
            [(r["username"], r["minutes"]) for r in rows], [("meera", 130), ("manoj", 50)]
        )
        rows = self.get("time-by-user/?date_from=2026-03-10&date_to=2026-03-10")
        self.assertEqual(
            [(r["username"], r["minutes"]) for r in rows], [("manoj", 50), ("meera", 30)]
        )

    def test_time_by_sprint(self):
        """Only time on tasks inside a sprint is counted per sprint."""
        self.assertEqual(
            self.get("time-by-sprint/"),
            [{"sprint_id": self.sprint.pk, "name": "S1", "minutes": 150}],
        )

    def test_workload_open_points_per_member_including_zero(self):
        """Open (not done) assigned work per assignable member; viewers excluded."""
        Task.objects.filter(pk=self.task.pk).update(assignee=self.member, story_points=3)
        Task.objects.filter(pk=self.task_two.pk).update(assignee=self.member, story_points=5)
        Task.objects.create(
            project=self.project,
            number=3,
            title="done",
            status="done",
            completed_at=NOW,
            assignee=self.member,
            story_points=8,
            reporter=self.owner,
        )
        rows = {row["username"]: row for row in self.get("workload/")}
        self.assertNotIn("vikram", rows)
        self.assertEqual((rows["meera"]["open_tasks"], rows["meera"]["open_points"]), (2, 8))
        self.assertEqual((rows["olivia"]["open_tasks"], rows["olivia"]["open_points"]), (0, 0))

    def test_reports_with_no_data_do_not_divide_by_zero(self):
        """Empty project: every report returns an empty or zero result."""
        empty = make_project(self.owner, key="EMP")
        client = self.client_for(self.owner)
        for path in ("time-by-task/", "time-by-user/", "time-by-sprint/"):
            response = client.get(f"/api/projects/{empty.pk}/reports/{path}")
            self.assertEqual(response.json()["results"], [])
        Task.objects.create(
            project=empty, number=1, title="t", reporter=self.owner, estimate_minutes=0
        )
        rows = client.get(f"/api/projects/{empty.pk}/reports/time-by-task/").json()["results"]
        self.assertIsNone(rows[0]["percent_of_estimate"])

    def test_reports_are_404_for_outsiders(self):
        """Reports go through the same permission module."""
        response = self.client_for(self.outsider).get(f"{self.base}/reports/workload/")
        self.assertEqual(response.status_code, 404)

    def test_report_query_count_is_constant(self):
        """No N+1: adding more tasks and entries does not add queries."""
        client = self.client_for(self.viewer)
        with CaptureQueriesContext(connection) as before:
            client.get(f"{self.base}/reports/time-by-task/")
            client.get(f"{self.base}/reports/workload/")
        for number in range(3, 13):
            task = Task.objects.create(
                project=self.project,
                number=number,
                title=f"t{number}",
                reporter=self.owner,
                assignee=self.member,
            )
            TimeEntry.objects.create(task=task, user=self.member, minutes=5, date=date(2026, 3, 1))
        with CaptureQueriesContext(connection) as after:
            client.get(f"{self.base}/reports/time-by-task/")
            client.get(f"{self.base}/reports/workload/")
        self.assertEqual(len(before), len(after))
