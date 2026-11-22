from datetime import UTC, date, datetime

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.testing import ProjectWithRolesMixin, make_project
from sprints.models import Sprint, SprintScopeChange
from tasks.models import Task

FIXED_NOW = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)


class SprintTestCase(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.client_for(self.manager)
        self.base = f"/api/projects/{self.project.pk}"

    def make_sprint(self, name="Sprint 1", status="planned", **extra) -> Sprint:
        return Sprint.objects.create(
            project=self.project,
            name=name,
            start_date=extra.pop("start_date", date(2026, 3, 2)),
            end_date=extra.pop("end_date", date(2026, 3, 13)),
            status=status,
            **extra,
        )

    def make_task(self, number, points=None, status="todo", sprint=None) -> Task:
        return Task.objects.create(
            project=self.project,
            number=number,
            title=f"T{number}",
            story_points=points,
            status=status,
            sprint=sprint,
            reporter=self.owner,
            assignee=self.member,
            completed_at=FIXED_NOW if status == "done" else None,
        )

    def add_to_sprint(self, sprint, number, client=None):
        return (client or self.client).post(
            f"{self.base}/sprints/{sprint.pk}/tasks/", {"task_number": number}
        )


class SprintCreateTests(SprintTestCase):
    def test_manager_creates_planned_sprint(self):
        """POST /sprints/ creates a planned sprint."""
        response = self.client.post(
            f"{self.base}/sprints/",
            {"name": "Sprint 1", "start_date": "2026-03-02", "end_date": "2026-03-13"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "planned")

    def test_end_before_start_is_rejected(self):
        """Dates must be ordered: start_date <= end_date."""
        response = self.client.post(
            f"{self.base}/sprints/",
            {"name": "Bad", "start_date": "2026-03-13", "end_date": "2026-03-02"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("end_date", response.json()["detail"])

    def test_single_day_sprint_is_allowed(self):
        """start_date == end_date is valid."""
        response = self.client.post(
            f"{self.base}/sprints/",
            {"name": "One day", "start_date": "2026-03-02", "end_date": "2026-03-02"},
        )
        self.assertEqual(response.status_code, 201)

    def test_database_rejects_unordered_dates(self):
        """The CheckConstraint guards the dates even if a service is bypassed."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_sprint(start_date=date(2026, 3, 5), end_date=date(2026, 3, 1))

    def test_sprint_permissions_by_role(self):
        """Matrix: only managers and owners manage sprints; outsiders get 404."""
        data = {"name": "S", "start_date": "2026-03-02", "end_date": "2026-03-13"}
        expected = {"viewer": 403, "member": 403, "manager": 201, "owner": 201}
        for role, code in expected.items():
            with self.subTest(role=role):
                response = self.client_for(self.users_by_role[role]).post(
                    f"{self.base}/sprints/", data
                )
                self.assertEqual(response.status_code, code)
        outsider = self.client_for(self.outsider).get(f"{self.base}/sprints/")
        self.assertEqual(outsider.status_code, 404)

    def test_members_can_read_sprints(self):
        """Every member can see sprints."""
        self.make_sprint()
        response = self.client_for(self.viewer).get(f"{self.base}/sprints/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)


class SprintPlanningTests(SprintTestCase):
    def test_add_task_to_planned_sprint_records_no_scope_change(self):
        """Planning before the start is not a scope change."""
        sprint = self.make_sprint()
        self.make_task(1, points=3)
        response = self.add_to_sprint(sprint, 1)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["sprint"]["id"], sprint.pk)
        self.assertFalse(SprintScopeChange.objects.exists())

    def test_task_can_be_in_only_one_sprint(self):
        """A second sprint cannot take a task that is already planned."""
        first, second = self.make_sprint("S1"), self.make_sprint("S2")
        self.make_task(1)
        self.add_to_sprint(first, 1)
        response = self.add_to_sprint(second, 1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "task_in_other_sprint")

    def test_done_task_cannot_be_planned(self):
        """Finished work does not go into a sprint."""
        sprint = self.make_sprint()
        self.make_task(1, status="done")
        self.assertEqual(self.add_to_sprint(sprint, 1).json()["code"], "task_done")

    def test_adding_and_removing_during_active_sprint_records_scope_changes(self):
        """Mid-sprint changes are stored with the task's points."""
        sprint = self.make_sprint(status="active")
        self.make_task(1, points=5)
        self.add_to_sprint(sprint, 1)
        response = self.client.delete(f"{self.base}/sprints/{sprint.pk}/tasks/1/")
        self.assertEqual(response.status_code, 204)
        changes = list(SprintScopeChange.objects.values_list("kind", "points"))
        self.assertEqual(changes, [("added", 5), ("removed", 5)])
        self.assertIsNone(Task.objects.get(number=1).sprint_id)

    def test_changing_points_of_active_sprint_task_records_the_difference(self):
        """Re-estimating 3 -> 5 records 'added 2'."""
        sprint = self.make_sprint(status="active")
        self.make_task(1, points=3, sprint=sprint)
        response = self.client_for(self.member).patch(
            f"{self.base}/tasks/1/", {"version": 1, "story_points": 5}
        )
        self.assertEqual(response.status_code, 200)
        change = SprintScopeChange.objects.get()
        self.assertEqual((change.kind, change.points), ("added", 2))

    def test_deleting_task_of_active_sprint_records_removal(self):
        """The burndown must know when scope disappears."""
        sprint = self.make_sprint(status="active")
        self.make_task(1, points=8, sprint=sprint)
        self.client.delete(f"{self.base}/tasks/1/")
        change = SprintScopeChange.objects.get()
        self.assertEqual((change.kind, change.points, change.task_number), ("removed", 8, 1))

    def test_task_from_other_project_cannot_be_planned(self):
        """IDOR: numbers are looked up inside the URL's project."""
        sprint = self.make_sprint()
        other = make_project(self.outsider, key="OTH")
        Task.objects.create(project=other, number=9, title="x", reporter=self.outsider)
        self.assertEqual(self.add_to_sprint(sprint, 9).status_code, 404)

    def test_backlog_lists_open_unplanned_tasks_by_priority(self):
        """Backlog = not done, no sprint, most important first."""
        sprint = self.make_sprint()
        Task.objects.create(
            project=self.project, number=1, title="low", priority="low", reporter=self.owner
        )
        Task.objects.create(
            project=self.project, number=2, title="urgent", priority="urgent", reporter=self.owner
        )
        self.make_task(3, sprint=sprint)
        self.make_task(4, status="done")
        response = self.client_for(self.viewer).get(f"{self.base}/backlog/")
        self.assertEqual([t["number"] for t in response.json()["results"]], [2, 1])

    def test_delete_planned_sprint_returns_tasks_to_backlog(self):
        """Deleting a plan does not delete work."""
        sprint = self.make_sprint()
        self.make_task(1, sprint=sprint)
        self.assertEqual(self.client.delete(f"{self.base}/sprints/{sprint.pk}/").status_code, 204)
        self.assertIsNone(Task.objects.get(number=1).sprint_id)

    def test_active_sprint_cannot_be_deleted(self):
        """Only planned sprints are deletable."""
        sprint = self.make_sprint(status="active")
        response = self.client.delete(f"{self.base}/sprints/{sprint.pk}/")
        self.assertEqual(response.status_code, 400)


class SprintStartTests(SprintTestCase):
    def test_start_sets_committed_points_null_counts_as_zero(self):
        """Points 3 + 5 + null = 8 committed."""
        sprint = self.make_sprint()
        for number, points in ((1, 3), (2, 5), (3, None)):
            self.make_task(number, points=points, sprint=sprint)
        response = self.client.post(f"{self.base}/sprints/{sprint.pk}/start/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "active")
        self.assertEqual(response.json()["committed_points"], 8)

    def test_second_active_sprint_is_rejected(self):
        """Only one active sprint per project."""
        self.make_sprint("Running", status="active")
        planned = self.make_sprint("Next")
        response = self.client.post(f"{self.base}/sprints/{planned.pk}/start/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "sprint_already_active")

    def test_database_allows_only_one_active_sprint_per_project(self):
        """The partial unique index rejects a second active row directly."""
        self.make_sprint("A", status="active")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_sprint("B", status="active")

    def test_active_sprints_in_different_projects_are_fine(self):
        """The constraint is per project."""
        self.make_sprint("A", status="active")
        other = make_project(self.owner, key="OTH")
        Sprint.objects.create(
            project=other,
            name="B",
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 13),
            status="active",
        )
        self.assertEqual(Sprint.objects.filter(status="active").count(), 2)

    def test_completed_sprint_cannot_be_started_again(self):
        """Start works only from planned."""
        sprint = self.make_sprint(status="completed")
        response = self.client.post(f"{self.base}/sprints/{sprint.pk}/start/")
        self.assertEqual(response.json()["code"], "sprint_not_planned")


class SprintCompleteTests(SprintTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sprint = self.make_sprint(status="active", committed_points=10)
        self.make_task(1, points=5, status="done", sprint=self.sprint)
        self.make_task(2, points=3, status="done", sprint=self.sprint)
        self.make_task(3, points=2, status="in_progress", sprint=self.sprint)

    def complete(self, **data):
        return self.client.post(f"{self.base}/sprints/{self.sprint.pk}/complete/", data)

    def test_complete_moves_unfinished_tasks_to_backlog(self):
        """Done tasks stay as history; unfinished tasks go back to the backlog."""
        response = self.complete(unfinished_action="backlog")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["completed_points"], 8)
        self.assertIsNone(Task.objects.get(number=3).sprint_id)
        self.assertEqual(Task.objects.get(number=1).sprint_id, self.sprint.pk)

    def test_complete_moves_unfinished_tasks_to_chosen_planned_sprint(self):
        """Unfinished work can roll into the next planned sprint."""
        next_sprint = self.make_sprint("Next")
        response = self.complete(unfinished_action="sprint", target_sprint_id=next_sprint.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Task.objects.get(number=3).sprint_id, next_sprint.pk)

    def test_target_must_be_a_planned_sprint(self):
        """Moving into a completed sprint or the same sprint is refused atomically."""
        old = self.make_sprint("Old", status="completed")
        response = self.complete(unfinished_action="sprint", target_sprint_id=old.pk)
        self.assertEqual(response.status_code, 400)
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, "active")
        self.assertEqual(Task.objects.get(number=3).sprint_id, self.sprint.pk)

    def test_choice_for_unfinished_tasks_is_required(self):
        """The caller must decide what happens to unfinished work."""
        self.assertEqual(self.complete().status_code, 400)
        response = self.complete(unfinished_action="sprint")
        self.assertEqual(response.status_code, 400)

    def test_only_active_sprint_can_be_completed(self):
        """Completing a planned sprint is refused."""
        planned = self.make_sprint("Planned")
        response = self.client.post(
            f"{self.base}/sprints/{planned.pk}/complete/", {"unfinished_action": "backlog"}
        )
        self.assertEqual(response.json()["code"], "sprint_not_active")

    def test_member_cannot_complete_sprint(self):
        """Matrix: sprint management is for managers and owners."""
        response = self.client_for(self.member).post(
            f"{self.base}/sprints/{self.sprint.pk}/complete/", {"unfinished_action": "backlog"}
        )
        self.assertEqual(response.status_code, 403)
