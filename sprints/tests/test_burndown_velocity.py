"""
Burndown and velocity checked against numbers calculated by hand.

Sprint used by most tests: Mon 2 Mar 2026 .. Fri 6 Mar 2026 (5 days),
committed 10 points (A=3, B=5, C=2). IST = UTC+5:30.
Ideal line: 10 * (end - day) / 4 -> [10, 7.5, 5, 2.5, 0].
"""

from datetime import UTC, date, datetime
from unittest import mock

from django.test import TestCase

from core.exceptions import BusinessRuleError
from core.testing import ProjectWithRolesMixin
from sprints.models import Sprint, SprintScopeChange
from sprints.selectors import burndown, velocity
from tasks.models import Task

IDEAL = [10, 7.5, 5, 2.5, 0]


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    """A March 2026 time given in IST, returned as an aware UTC datetime."""
    total_minutes = hour * 60 + minute - 330  # IST is 5h30m ahead of UTC
    utc_day, utc_minutes = (
        (day, total_minutes) if total_minutes >= 0 else (day - 1, total_minutes + 1440)
    )
    return datetime(2026, 3, utc_day, utc_minutes // 60, utc_minutes % 60, tzinfo=UTC)


class BurndownTests(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sprint = Sprint.objects.create(
            project=self.project,
            name="S1",
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 6),
            status=Sprint.Status.ACTIVE,
            committed_points=10,
        )
        self.tasks = {
            name: Task.objects.create(
                project=self.project,
                number=number,
                title=name,
                story_points=points,
                sprint=self.sprint,
                reporter=self.owner,
            )
            for number, (name, points) in enumerate((("A", 3), ("B", 5), ("C", 2)), start=1)
        }

    def complete(self, name: str, at: datetime) -> None:
        Task.objects.filter(pk=self.tasks[name].pk).update(status="done", completed_at=at)

    def scope(self, kind: str, points: int, at: datetime) -> None:
        SprintScopeChange.objects.create(
            sprint=self.sprint, task_number=99, kind=kind, points=points, at=at
        )

    def series(self, today: datetime) -> tuple[list, list]:
        with mock.patch("core.clock.now", return_value=today):
            days = burndown(self.sprint)["days"]
        return [d["remaining"] for d in days], [d["ideal"] for d in days]

    def test_no_scope_change(self):
        """3 pts done Tue, 5 pts done Thu -> [10, 7, 7, 2, 2]."""
        self.complete("A", ist(3, 10))
        self.complete("B", ist(5, 15))
        remaining, ideal = self.series(ist(6, 12))
        self.assertEqual(remaining, [10, 7, 7, 2, 2])
        self.assertEqual(ideal, IDEAL)

    def test_task_added_mid_sprint(self):
        """4 pts added Wed raise the line from Wed on: [10, 10, 14, 14, 14]."""
        self.scope("added", 4, ist(4, 9))
        remaining, _ = self.series(ist(6, 12))
        self.assertEqual(remaining, [10, 10, 14, 14, 14])

    def test_task_removed_mid_sprint(self):
        """2 pts removed Tue lower the line from Tue on: [10, 8, 8, 8, 8]."""
        self.scope("removed", 2, ist(3, 9))
        remaining, _ = self.series(ist(6, 12))
        self.assertEqual(remaining, [10, 8, 8, 8, 8])

    def test_completion_on_first_and_last_day(self):
        """Done at 00:10 IST Mon counts on Mon; done at 23:50 IST Fri counts on Fri."""
        self.complete("A", ist(2, 0, 10))
        self.complete("B", ist(6, 23, 50))
        remaining, _ = self.series(ist(6, 23, 55))
        self.assertEqual(remaining, [7, 7, 7, 7, 2])

    def test_timezone_boundary_uses_project_timezone(self):
        """18:20 UTC Mon = 23:50 IST Mon (counts Mon); 18:40 UTC Mon = 00:10 IST Tue."""
        self.complete("A", datetime(2026, 3, 2, 18, 20, tzinfo=UTC))
        self.complete("B", datetime(2026, 3, 2, 18, 40, tzinfo=UTC))
        remaining, _ = self.series(ist(6, 12))
        self.assertEqual(remaining, [7, 2, 2, 2, 2])

    def test_future_days_have_no_actual_value(self):
        """On Wednesday, Thu and Fri are still unknown (None) but keep the ideal line."""
        remaining, ideal = self.series(ist(4, 12))
        self.assertEqual(remaining, [10, 10, 10, None, None])
        self.assertEqual(ideal, IDEAL)

    def test_zero_point_sprint_does_not_divide_by_zero(self):
        """committed 0 gives a flat zero line."""
        self.sprint.committed_points = 0
        Task.objects.update(story_points=None)
        remaining, ideal = self.series(ist(6, 12))
        self.assertEqual(remaining, [0, 0, 0, 0, 0])
        self.assertEqual(ideal, [0, 0, 0, 0, 0])

    def test_single_day_sprint_has_ideal_zero(self):
        """start == end: no division by zero, ideal 0 on the end date."""
        self.sprint.end_date = date(2026, 3, 2)
        remaining, ideal = self.series(ist(2, 12))
        self.assertEqual(remaining, [10])
        self.assertEqual(ideal, [0.0])

    def test_planned_sprint_has_no_burndown(self):
        """Burndown starts only when the sprint starts."""
        self.sprint.status = Sprint.Status.PLANNED
        with self.assertRaises(BusinessRuleError):
            burndown(self.sprint)

    def test_burndown_endpoint_for_members_and_404_for_outsiders(self):
        """GET /sprints/<id>/burndown/ is readable by any member only."""
        url = f"/api/projects/{self.project.pk}/sprints/{self.sprint.pk}/burndown/"
        with mock.patch("core.clock.now", return_value=ist(6, 12)):
            response = self.client_for(self.viewer).get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["days"][0], {"date": "2026-03-02", "ideal": 10.0, "remaining": 10}
        )
        self.assertEqual(self.client_for(self.outsider).get(url).status_code, 404)


class VelocityTests(ProjectWithRolesMixin, TestCase):
    def make_completed(self, name: str, points: int, day: int) -> Sprint:
        return Sprint.objects.create(
            project=self.project,
            name=name,
            start_date=date(2026, 1, day),
            end_date=date(2026, 1, day + 1),
            status=Sprint.Status.COMPLETED,
            completed_points=points,
            completed_at=datetime(2026, 1, day + 1, 12, tzinfo=UTC),
        )

    def test_average_is_zero_without_completed_sprints(self):
        """No completed sprint -> average 0, empty history."""
        self.assertEqual(velocity(self.project.pk), {"sprints": [], "average_velocity": 0})

    def test_average_with_one_sprint(self):
        """One sprint of 7 points -> average 7."""
        self.make_completed("S1", 7, 1)
        self.assertEqual(velocity(self.project.pk)["average_velocity"], 7)

    def test_average_uses_last_three_completed_sprints(self):
        """Points 20, 8, 5, 6 (oldest first) -> mean of 8, 5, 6 = 6.33."""
        for index, points in enumerate((20, 8, 5, 6)):
            self.make_completed(f"S{index + 1}", points, 1 + index * 3)
        Sprint.objects.create(
            project=self.project,
            name="Active",
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 5),
            status=Sprint.Status.ACTIVE,
        )
        result = velocity(self.project.pk)
        self.assertEqual(result["average_velocity"], 6.33)
        self.assertEqual([s["completed_points"] for s in result["sprints"]], [20, 8, 5, 6])

    def test_velocity_endpoint(self):
        """GET /velocity/ is readable by members."""
        self.make_completed("S1", 4, 1)
        response = self.client_for(self.viewer).get(f"/api/projects/{self.project.pk}/velocity/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["average_velocity"], 4)
