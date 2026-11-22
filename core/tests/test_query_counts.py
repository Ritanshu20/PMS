"""
Phase 16: exact query budgets for the heaviest pages and API endpoints.

If a change adds a query (for example an N+1 in a template loop), the matching
test fails and shows the new count. Each test also adds 10 more tasks and
checks that the budget does not move.
"""

from datetime import timedelta

from django.test import Client, TestCase

from activity.models import ActivityLog
from core import clock
from core.testing import ProjectWithRolesMixin
from projects.models import Project
from sprints.models import Sprint
from tasks.models import Comment, Label, Task, TaskDependency, TimeEntry

# page or endpoint -> queries (session/user/membership/unread-badge included for pages)
PAGE_BUDGETS = {
    "board/": 8,
    "backlog/": 8,
    "tasks/1/": 11,
    "dashboard/": 11,
    "reports/time/": 9,
    "sprint": 11,
}
API_BUDGETS = {"tasks/": 6, "board/": 5, "dashboard/": 7, "backlog/": 6}
MY_TASKS_BUDGET = 5


class QueryBudgetTests(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        today = clock.today()
        self.sprint = Sprint.objects.create(
            project=self.project,
            name="S1",
            start_date=today - timedelta(days=2),
            end_date=today + timedelta(days=5),
            status="active",
            committed_points=8,
        )
        self.label = Label.objects.create(project=self.project, name="bug")
        self.next_number = 1
        for _ in range(3):
            self.add_task()
        first, second = Task.objects.filter(project=self.project).order_by("number")[:2]
        TaskDependency.objects.create(project=self.project, blocker=first, blocked=second)
        Comment.objects.create(task=first, author=self.member, body="hi @manoj")
        TimeEntry.objects.create(task=first, user=self.member, minutes=30, date=today)
        ActivityLog.objects.create(
            project=self.project,
            actor=self.member,
            verb="task.updated",
            target_type="task",
            target_id=first.pk,
        )
        self.base = f"/projects/{self.project.pk}/"

    def add_task(self) -> Task:
        number = self.next_number
        self.next_number += 1
        task = Task.objects.create(
            project=self.project,
            number=number,
            title=f"T{number}",
            story_points=3,
            sprint=self.sprint if number % 2 else None,
            assignee=self.member,
            reporter=self.owner,
        )
        task.labels.add(self.label)
        Project.objects.filter(pk=self.project.pk).update(task_counter=number)
        return task

    def page_url(self, key: str) -> str:
        if key == "sprint":
            return f"{self.base}sprints/{self.sprint.pk}/"
        return self.base + key

    def assert_budget(self, fetch, budget: int) -> None:
        with self.assertNumQueries(budget):
            self.assertEqual(fetch().status_code, 200)
        for _ in range(10):
            self.add_task()
        with self.assertNumQueries(budget):
            fetch()

    def test_page_query_budgets(self):
        """Board, backlog, task detail, dashboard, time report and sprint pages stay fixed."""
        client = Client()
        client.force_login(self.member)
        for key, budget in PAGE_BUDGETS.items():
            with self.subTest(page=key):
                self.assert_budget(lambda key=key: client.get(self.page_url(key)), budget)

    def test_my_tasks_page_budget(self):
        """My tasks across projects: fixed number of queries."""
        client = Client()
        client.force_login(self.member)
        self.assert_budget(lambda: client.get("/my-tasks/"), MY_TASKS_BUDGET)

    def test_api_query_budgets(self):
        """Main API reads stay fixed: list, board, dashboard, backlog."""
        client = self.client_for(self.member)
        for key, budget in API_BUDGETS.items():
            with self.subTest(endpoint=key):
                url = f"/api/projects/{self.project.pk}/{key}"
                self.assert_budget(lambda url=url: client.get(url), budget)
