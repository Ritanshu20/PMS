from datetime import UTC, datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.testing import ProjectWithRolesMixin
from tasks.models import Task
from tasks.workflow import STATUS_ORDER, allowed_next_statuses

FIXED_NOW = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)


class AllowedTransitionTableTests(TestCase):
    def test_forward_one_step_and_backward_any_step(self):
        """The transition table: next status forward, any earlier status backward."""
        expected = {
            "backlog": {"todo"},
            "todo": {"backlog", "in_progress"},
            "in_progress": {"backlog", "todo", "in_review"},
            "in_review": {"backlog", "todo", "in_progress", "done"},
            "done": {"backlog", "todo", "in_progress", "in_review"},
        }
        for status, allowed in expected.items():
            with self.subTest(status=status):
                self.assertEqual(allowed_next_statuses(status), allowed)


class WorkflowApiTestCase(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.client_for(self.member)

    def url(self, number: int, suffix: str = "") -> str:
        return f"/api/projects/{self.project.pk}/tasks/{number}/{suffix}"

    def make_task(self, number: int, status: str = "backlog", assignee=None, position: int = 0):
        return Task.objects.create(
            project=self.project,
            number=number,
            title=f"T{number}",
            status=status,
            assignee=assignee,
            reporter=self.owner,
            position=position,
            completed_at=FIXED_NOW if status == "done" else None,
        )

    def move(self, number: int, status: str, position: int = 0, client=None):
        version = Task.objects.get(project=self.project, number=number).version
        return (client or self.client).post(
            self.url(number, "move/"), {"version": version, "status": status, "position": position}
        )


class TransitionRuleTests(WorkflowApiTestCase):
    def test_every_allowed_and_blocked_transition_through_the_api(self):
        """All 20 from/to pairs: allowed ones succeed, others give invalid_transition."""
        number = 0
        for from_status in STATUS_ORDER:
            for to_status in STATUS_ORDER:
                if from_status == to_status:
                    continue
                number += 1
                self.make_task(number, status=from_status, assignee=self.member)
                with self.subTest(from_status=from_status, to_status=to_status):
                    response = self.move(number, to_status)
                    if to_status in allowed_next_statuses(from_status):
                        self.assertEqual(response.status_code, 200, response.content)
                        self.assertEqual(response.json()["status"], to_status)
                    else:
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(response.json()["code"], "invalid_transition")

    def test_in_progress_requires_an_assignee(self):
        """Moving to in_progress without an assignee is rejected."""
        self.make_task(1, status="todo")
        response = self.move(1, "in_progress")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "assignee_required")

    def test_assign_and_start_in_one_update_is_allowed(self):
        """A PATCH may set the assignee and move to in_progress together."""
        self.make_task(1, status="todo")
        response = self.client.patch(
            self.url(1), {"version": 1, "status": "in_progress", "assignee_id": self.member.pk}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "in_progress")

    def test_entering_done_sets_completed_at_and_reopen_clears_it(self):
        """completed_at follows the done status (fixed clock)."""
        self.make_task(1, status="in_review", assignee=self.member)
        with mock.patch("core.clock.now", return_value=FIXED_NOW):
            done = self.move(1, "done")
        self.assertEqual(done.json()["completed_at"], "2026-03-10T11:30:00+05:30")
        reopened = self.move(1, "todo")
        self.assertEqual(reopened.status_code, 200)
        self.assertIsNone(reopened.json()["completed_at"])

    def test_viewer_cannot_change_status(self):
        """Matrix: viewers cannot move cards."""
        self.make_task(1, status="backlog")
        response = self.move(1, "todo", client=self.client_for(self.viewer))
        self.assertEqual(response.status_code, 403)

    def test_move_with_stale_version_returns_409(self):
        """Board moves use optimistic locking too."""
        self.make_task(1, status="backlog")
        response = self.client.post(
            self.url(1, "move/"), {"version": 99, "status": "todo", "position": 0}
        )
        self.assertEqual(response.status_code, 409)


class BoardOrderingTests(WorkflowApiTestCase):
    def column(self, status: str) -> list[int]:
        return list(
            Task.objects.filter(project=self.project, status=status)
            .order_by("position")
            .values_list("number", flat=True)
        )

    def test_reorder_within_a_column(self):
        """Moving C to the top of [A, B, C] gives [C, A, B] with positions 0..2."""
        for number in (1, 2, 3):
            self.make_task(number, status="todo", position=number - 1)
        response = self.move(3, "todo", position=0)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.column("todo"), [3, 1, 2])
        positions = list(
            Task.objects.filter(status="todo")
            .order_by("position")
            .values_list("position", flat=True)
        )
        self.assertEqual(positions, [0, 1, 2])

    def test_move_into_another_column_at_position(self):
        """Card from backlog lands between the existing todo cards."""
        self.make_task(1, status="todo", position=0)
        self.make_task(2, status="todo", position=1)
        self.make_task(3, status="backlog", position=0)
        self.move(3, "todo", position=1)
        self.assertEqual(self.column("todo"), [1, 3, 2])
        self.assertEqual(self.column("backlog"), [])

    def test_position_beyond_column_end_is_clamped(self):
        """Position 50 in a 1-card column means 'at the end'."""
        self.make_task(1, status="todo", position=0)
        self.make_task(2, status="backlog", position=0)
        response = self.move(2, "todo", position=50)
        self.assertEqual(response.json()["position"], 1)

    def test_status_change_by_patch_appends_to_end_of_new_column(self):
        """A non-board status change puts the card at the bottom of the new column."""
        self.make_task(1, status="todo", position=0)
        self.make_task(2, status="backlog", position=0)
        self.client.patch(self.url(2), {"version": 1, "status": "todo"})
        self.assertEqual(self.column("todo"), [1, 2])

    def test_board_groups_tasks_by_status_in_position_order(self):
        """GET /board/ returns every column, each ordered by position."""
        self.make_task(1, status="todo", position=1)
        self.make_task(2, status="todo", position=0)
        self.make_task(3, status="done", position=0)
        response = self.client.get(f"/api/projects/{self.project.pk}/board/")
        columns = response.json()["columns"]
        self.assertEqual(list(columns), STATUS_ORDER)
        self.assertEqual([t["number"] for t in columns["todo"]], [2, 1])
        self.assertEqual([t["number"] for t in columns["done"]], [3])
        self.assertEqual(columns["backlog"], [])

    def test_board_query_count_does_not_grow_with_tasks(self):
        """No N+1 on the board."""
        self.make_task(1, status="todo", assignee=self.member)
        with CaptureQueriesContext(connection) as few:
            self.client.get(f"/api/projects/{self.project.pk}/board/")
        for number in range(2, 12):
            self.make_task(number, status="in_progress", assignee=self.manager)
        with CaptureQueriesContext(connection) as many:
            self.client.get(f"/api/projects/{self.project.pk}/board/")
        self.assertEqual(len(few), len(many))

    def test_outsider_cannot_see_board(self):
        """Non-members get 404 for the board."""
        response = self.client_for(self.outsider).get(f"/api/projects/{self.project.pk}/board/")
        self.assertEqual(response.status_code, 404)
