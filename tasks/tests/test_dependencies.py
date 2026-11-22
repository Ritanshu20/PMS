from datetime import UTC, datetime

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.testing import ProjectWithRolesMixin, make_project
from tasks.dependency_services import would_create_cycle
from tasks.models import Task, TaskDependency

FIXED_NOW = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)


class CycleDetectionUnitTests(TestCase):
    def test_new_edge_closing_a_loop_is_a_cycle(self):
        """A->B->C exists; C->A would close the loop."""
        graph = {1: {2}, 2: {3}}
        self.assertTrue(would_create_cycle(graph, blocker_id=3, blocked_id=1))

    def test_new_edge_along_the_chain_is_not_a_cycle(self):
        """A->B->C exists; A->C is a shortcut, not a loop."""
        graph = {1: {2}, 2: {3}}
        self.assertFalse(would_create_cycle(graph, blocker_id=1, blocked_id=3))

    def test_self_edge_is_a_cycle(self):
        """A->A is the smallest loop."""
        self.assertTrue(would_create_cycle({}, blocker_id=1, blocked_id=1))

    def test_diamond_graph_has_no_cycle(self):
        """A->B, A->C, B->D, C->D: adding A->D is fine; D->A is not."""
        graph = {1: {2, 3}, 2: {4}, 3: {4}}
        self.assertFalse(would_create_cycle(graph, blocker_id=1, blocked_id=4))
        self.assertTrue(would_create_cycle(graph, blocker_id=4, blocked_id=1))


class DependencyApiTests(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.client_for(self.member)
        self.tasks = {
            number: Task.objects.create(
                project=self.project,
                number=number,
                title=f"T{number}",
                reporter=self.owner,
                assignee=self.member,
            )
            for number in (1, 2, 3, 4)
        }

    def deps_url(self, blocked: int, blocker: int | None = None) -> str:
        base = f"/api/projects/{self.project.pk}/tasks/{blocked}/dependencies/"
        return f"{base}{blocker}/" if blocker else base

    def add(self, blocker: int, blocked: int, client=None):
        return (client or self.client).post(self.deps_url(blocked), {"blocker_number": blocker})

    def test_valid_chain_is_accepted_and_shown_on_both_tasks(self):
        """1 blocks 2 blocks 3: task 2 shows blocked_by [1] and blocks [3]."""
        self.assertEqual(self.add(1, 2).status_code, 201)
        response = self.add(2, 3)
        self.assertEqual(response.status_code, 201)
        task_two = self.client.get(f"/api/projects/{self.project.pk}/tasks/2/").json()
        self.assertEqual([t["key"] for t in task_two["blocked_by"]], ["WEB-1"])
        self.assertEqual([t["key"] for t in task_two["blocks"]], ["WEB-3"])

    def test_self_dependency_is_rejected(self):
        """A task cannot block itself."""
        response = self.add(1, 1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "self_dependency")

    def test_duplicate_dependency_is_rejected(self):
        """The same edge cannot be added twice."""
        self.add(1, 2)
        response = self.add(1, 2)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "duplicate_dependency")

    def test_direct_cycle_is_rejected(self):
        """1 blocks 2, then 2 blocks 1 is refused."""
        self.add(1, 2)
        response = self.add(2, 1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "dependency_cycle")

    def test_long_cycle_is_rejected(self):
        """1>2>3>4, then 4 blocks 1 is refused."""
        self.add(1, 2)
        self.add(2, 3)
        self.add(3, 4)
        response = self.add(4, 1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "dependency_cycle")

    def test_cross_project_dependency_is_impossible(self):
        """A task number from another project is a 404 inside this project."""
        other = make_project(self.outsider, key="OTH")
        Task.objects.create(project=other, number=99, title="x", reporter=self.outsider)
        response = self.add(99, 1)
        self.assertEqual(response.status_code, 404)

    def test_done_is_rejected_while_blockers_are_open_then_allowed(self):
        """Task 2 cannot be done while task 1 is open; it can once task 1 is done."""
        self.add(1, 2)
        Task.objects.filter(number=2).update(status="in_review")
        blocked = self.client.patch(
            f"/api/projects/{self.project.pk}/tasks/2/", {"version": 1, "status": "done"}
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["code"], "blocked")
        self.assertEqual(blocked.json()["details"]["blocked_by"], ["WEB-1"])
        self.assertIn("WEB-1", blocked.json()["detail"])
        Task.objects.filter(number=1).update(status="done", completed_at=FIXED_NOW)
        allowed = self.client.patch(
            f"/api/projects/{self.project.pk}/tasks/2/", {"version": 1, "status": "done"}
        )
        self.assertEqual(allowed.status_code, 200, allowed.content)

    def test_done_task_cannot_get_an_open_blocker(self):
        """Otherwise a done task would have an open blocker."""
        Task.objects.filter(number=2).update(status="done", completed_at=FIXED_NOW)
        response = self.add(1, 2)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "blocked_task_done")

    def test_deleting_a_task_removes_its_dependency_edges(self):
        """Documented behaviour: edges disappear with the task."""
        self.add(1, 2)
        self.add(2, 3)
        self.client_for(self.owner).delete(f"/api/projects/{self.project.pk}/tasks/2/")
        self.assertFalse(TaskDependency.objects.exists())

    def test_remove_dependency(self):
        """DELETE removes one edge; a missing edge is 404."""
        self.add(1, 2)
        self.assertEqual(self.client.delete(self.deps_url(2, 1)).status_code, 204)
        self.assertEqual(self.client.delete(self.deps_url(2, 1)).status_code, 404)

    def test_dependency_permissions_by_role(self):
        """Matrix: members and above manage dependencies; viewers cannot; outsiders get 404."""
        self.assertEqual(self.add(1, 2, client=self.client_for(self.viewer)).status_code, 403)
        self.assertEqual(self.add(1, 2, client=self.client_for(self.outsider)).status_code, 404)
        self.assertEqual(self.add(1, 2, client=self.client_for(self.member)).status_code, 201)

    def test_database_rejects_self_dependency_directly(self):
        """The CheckConstraint is a second line of defence behind the service."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskDependency.objects.create(
                project=self.project, blocker=self.tasks[1], blocked=self.tasks[1]
            )
