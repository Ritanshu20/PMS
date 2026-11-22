from datetime import UTC, date, datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from activity.models import ActivityLog
from core.testing import ProjectWithRolesMixin, add_member, make_project, make_user
from projects.models import Project, ProjectMember
from tasks.models import Label, Task

FIXED_NOW = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)  # 11:30 IST on 10 Mar 2026


class TaskApiTestCase(ProjectWithRolesMixin, TestCase):
    def tasks_url(self, number: int | None = None, project: Project | None = None) -> str:
        project_id = (project or self.project).pk
        base = f"/api/projects/{project_id}/tasks/"
        return f"{base}{number}/" if number else base

    def create_task(self, user=None, **data):
        data.setdefault("title", "Write docs")
        return self.client_for(user or self.member).post(self.tasks_url(), data)


class TaskNumberingTests(TaskApiTestCase):
    def test_numbers_are_sequential_per_project_and_key_is_shown(self):
        """Tasks get 1, 2, 3 and the visible key is WEB-<number>."""
        numbers = [self.create_task().json()["number"] for _ in range(3)]
        self.assertEqual(numbers, [1, 2, 3])
        self.assertEqual(self.create_task().json()["key"], "WEB-4")

    def test_each_project_has_its_own_sequence(self):
        """Numbering restarts at 1 in another project."""
        other = make_project(self.member, key="APP")
        self.create_task()
        response = self.client_for(self.member).post(self.tasks_url(project=other), {"title": "x"})
        self.assertEqual(response.json()["key"], "APP-1")

    def test_rolled_back_creation_does_not_consume_a_number(self):
        """If creation fails after the counter increment, the number is reused."""
        with (
            mock.patch("tasks.services.log_activity", side_effect=RuntimeError("boom")),
            self.assertLogs("django.request", level="ERROR"),
            self.assertRaises(RuntimeError),
        ):
            self.create_task()
        self.assertEqual(Project.objects.get(pk=self.project.pk).task_counter, 0)
        self.assertEqual(self.create_task().json()["number"], 1)


class TaskPermissionTests(TaskApiTestCase):
    def test_create_task_permission_by_role(self):
        """Matrix: viewers cannot create tasks; everyone else can."""
        expected = {"viewer": 403, "member": 201, "manager": 201, "owner": 201}
        for role, code in expected.items():
            with self.subTest(role=role):
                self.assertEqual(self.create_task(self.users_by_role[role]).status_code, code)

    def test_edit_task_permission_by_role(self):
        """Matrix: viewers cannot edit tasks."""
        self.create_task()
        for role, code in {"viewer": 403, "member": 200, "manager": 200, "owner": 200}.items():
            with self.subTest(role=role):
                version = Task.objects.get(number=1).version
                response = self.client_for(self.users_by_role[role]).patch(
                    self.tasks_url(1), {"version": version, "title": f"by {role}"}
                )
                self.assertEqual(response.status_code, code)

    def test_member_deletes_own_task_but_not_others(self):
        """Matrix: members delete only tasks they created; managers delete any."""
        self.create_task(self.member)
        self.create_task(self.owner)
        client = self.client_for(self.member)
        self.assertEqual(client.delete(self.tasks_url(2)).status_code, 403)
        self.assertEqual(client.delete(self.tasks_url(1)).status_code, 204)
        self.assertEqual(self.client_for(self.manager).delete(self.tasks_url(2)).status_code, 204)

    def test_viewer_cannot_delete_any_task(self):
        """Matrix: viewers never delete."""
        self.create_task(self.member)
        self.assertEqual(self.client_for(self.viewer).delete(self.tasks_url(1)).status_code, 403)

    def test_outsider_gets_404_for_tasks_of_a_real_project(self):
        """IDOR: user B cannot read or change user A's tasks by guessing ids."""
        self.create_task()
        client = self.client_for(self.outsider)
        self.assertEqual(client.get(self.tasks_url()).status_code, 404)
        self.assertEqual(client.get(self.tasks_url(1)).status_code, 404)
        self.assertEqual(client.patch(self.tasks_url(1), {"version": 1}).status_code, 404)
        self.assertEqual(client.delete(self.tasks_url(1)).status_code, 404)

    def test_task_of_other_project_is_not_reachable_through_my_project(self):
        """IDOR: task numbers are looked up inside the URL's project only."""
        other = make_project(self.outsider, key="OTH")
        Task.objects.create(project=other, number=7, title="secret", reporter=self.outsider)
        response = self.client_for(self.member).get(self.tasks_url(7))
        self.assertEqual(response.status_code, 404)


class TaskAssigneeTests(TaskApiTestCase):
    def test_assignee_must_be_a_non_viewer_member(self):
        """Viewers and outsiders cannot be assigned."""
        for user in (self.viewer, self.outsider):
            with self.subTest(user=user.username):
                response = self.create_task(assignee_id=user.pk)
                self.assertEqual(response.status_code, 400)
                self.assertIn("assignee_id", response.json()["detail"])

    def test_member_can_be_assigned(self):
        """Owners, managers and members are valid assignees."""
        response = self.create_task(assignee_id=self.manager.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["assignee"]["username"], "manoj")


class OptimisticLockingTests(TaskApiTestCase):
    def test_update_with_current_version_succeeds_and_bumps_version(self):
        """A save based on the latest version is applied and version goes up by one."""
        self.create_task()
        response = self.client_for(self.member).patch(
            self.tasks_url(1), {"version": 1, "title": "New title"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 2)
        self.assertEqual(response.json()["title"], "New title")

    def test_stale_version_returns_409_with_current_task(self):
        """Second editor with an old version gets 409 and the current data."""
        self.create_task()
        self.client_for(self.member).patch(self.tasks_url(1), {"version": 1, "title": "First"})
        response = self.client_for(self.manager).patch(
            self.tasks_url(1), {"version": 1, "title": "Second"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "version_conflict")
        self.assertEqual(response.json()["details"]["current"]["title"], "First")
        self.assertEqual(response.json()["details"]["current"]["version"], 2)
        self.assertEqual(Task.objects.get(number=1).title, "First")

    def test_update_without_version_is_rejected(self):
        """The version field is mandatory for every update."""
        self.create_task()
        response = self.client_for(self.member).patch(self.tasks_url(1), {"title": "x"})
        self.assertEqual(response.status_code, 400)


class TaskFilterTests(TaskApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bug = Label.objects.create(project=self.project, name="bug", color="#ff0000")
        specs = [
            # title, status, priority, assignee, due
            ("Fix login bug", "todo", "urgent", self.member, date(2026, 3, 1)),
            ("Write docs", "backlog", "low", None, date(2026, 3, 20)),
            ("Deploy app", "done", "high", self.manager, date(2026, 3, 2)),
            ("Design board", "backlog", "medium", self.member, None),
        ]
        for number, (title, status, priority, assignee, due) in enumerate(specs, start=1):
            task = Task.objects.create(
                project=self.project,
                number=number,
                title=title,
                status=status,
                priority=priority,
                assignee=assignee,
                due_date=due,
                reporter=self.owner,
                completed_at=FIXED_NOW if status == "done" else None,
            )
            if number == 1:
                task.labels.add(self.bug)

    def numbers(self, query: str, user=None) -> list[int]:
        response = self.client_for(user or self.member).get(self.tasks_url() + query)
        self.assertEqual(response.status_code, 200, response.content)
        return [task["number"] for task in response.json()["results"]]

    def test_filter_by_status_priority_assignee_and_label(self):
        """Each filter narrows the list correctly."""
        self.assertEqual(self.numbers("?status=backlog"), [2, 4])
        self.assertEqual(self.numbers("?status=todo,done"), [1, 3])
        self.assertEqual(self.numbers("?priority=urgent,high"), [1, 3])
        self.assertEqual(self.numbers("?assignee=me"), [1, 4])
        self.assertEqual(self.numbers("?assignee=none"), [2])
        self.assertEqual(self.numbers(f"?assignee={self.manager.pk}"), [3])
        self.assertEqual(self.numbers(f"?label={self.bug.pk}"), [1])

    def test_filter_by_due_dates_and_overdue_with_fixed_clock(self):
        """Overdue = due before today (IST) and not done; done tasks are never overdue."""
        with mock.patch("core.clock.now", return_value=FIXED_NOW):
            self.assertEqual(self.numbers("?overdue=true"), [1])
        self.assertEqual(self.numbers("?due_before=2026-03-02"), [1, 3])
        self.assertEqual(self.numbers("?due_after=2026-03-02"), [2, 3])

    def test_text_search_matches_title_and_task_key(self):
        """?q= searches title/description and also accepts a key like WEB-3."""
        self.assertEqual(self.numbers("?q=bug"), [1])
        self.assertEqual(self.numbers("?q=WEB-3"), [3])

    def test_ordering_by_priority_uses_business_order_not_alphabet(self):
        """urgent > high > medium > low, not alphabetical."""
        self.assertEqual(self.numbers("?ordering=-priority"), [1, 3, 4, 2])

    def test_invalid_filter_values_return_400(self):
        """Unknown statuses or orderings are rejected, not ignored."""
        for query in ("?status=nope", "?ordering=password", "?assignee=abc"):
            with self.subTest(query=query):
                response = self.client_for(self.member).get(self.tasks_url() + query)
                self.assertEqual(response.status_code, 400)

    def test_pagination_returns_count_and_page_size(self):
        """Lists are paginated with ?page_size."""
        response = self.client_for(self.member).get(self.tasks_url() + "?page_size=2")
        self.assertEqual(response.json()["count"], 4)
        self.assertEqual(len(response.json()["results"]), 2)
        self.assertIsNotNone(response.json()["next"])

    def test_task_list_query_count_does_not_grow_with_tasks(self):
        """No N+1: 4 tasks or 14 tasks cost the same number of queries."""
        client = self.client_for(self.member)
        with CaptureQueriesContext(connection) as few:
            client.get(self.tasks_url())
        for number in range(5, 15):
            task = Task.objects.create(
                project=self.project,
                number=number,
                title=f"t{number}",
                reporter=self.owner,
                assignee=self.member,
            )
            task.labels.add(self.bug)
        with CaptureQueriesContext(connection) as many:
            response = client.get(self.tasks_url())
        self.assertEqual(response.json()["count"], 14)
        self.assertEqual(len(few), len(many))


class LabelTests(TaskApiTestCase):
    def labels_url(self) -> str:
        return f"/api/projects/{self.project.pk}/labels/"

    def test_only_managers_and_owners_manage_labels(self):
        """Matrix: labels are managed by managers and owners."""
        expected = {"viewer": 403, "member": 403, "manager": 201, "owner": 201}
        for role, code in expected.items():
            with self.subTest(role=role):
                response = self.client_for(self.users_by_role[role]).post(
                    self.labels_url(), {"name": f"l-{role}", "color": "#112233"}
                )
                self.assertEqual(response.status_code, code)

    def test_label_names_are_unique_ignoring_case(self):
        """'Bug' and 'bug' cannot coexist in one project."""
        client = self.client_for(self.manager)
        self.assertEqual(client.post(self.labels_url(), {"name": "Bug"}).status_code, 201)
        self.assertEqual(client.post(self.labels_url(), {"name": "bug"}).status_code, 400)

    def test_label_from_another_project_cannot_be_attached(self):
        """IDOR: label ids are checked against the task's project."""
        other = make_project(self.outsider, key="OTH")
        foreign = Label.objects.create(project=other, name="x")
        response = self.create_task(label_ids=[foreign.pk])
        self.assertEqual(response.status_code, 400)

    def test_labels_can_be_set_on_update(self):
        """label_ids replaces the task's labels."""
        label = Label.objects.create(project=self.project, name="ui")
        self.create_task()
        response = self.client_for(self.member).patch(
            self.tasks_url(1), {"version": 1, "label_ids": [label.pk]}
        )
        self.assertEqual([item["name"] for item in response.json()["labels"]], ["ui"])


class TaskActivityTests(TaskApiTestCase):
    def test_create_update_and_delete_write_activity_rows(self):
        """Every task state change is recorded in the activity log."""
        self.create_task()
        self.client_for(self.member).patch(self.tasks_url(1), {"version": 1, "priority": "high"})
        self.client_for(self.member).delete(self.tasks_url(1))
        verbs = list(
            ActivityLog.objects.filter(project=self.project, target_type="task")
            .order_by("id")
            .values_list("verb", flat=True)
        )
        self.assertEqual(verbs, ["task.created", "task.updated", "task.deleted"])
        update = ActivityLog.objects.get(verb="task.updated")
        self.assertEqual(update.changes, {"priority": "high"})


class AssignmentNotificationTests(TaskApiTestCase):
    def test_assigning_someone_else_notifies_them(self):
        """The new assignee gets an 'assigned' notification."""
        from activity.models import Notification

        self.create_task(self.member, assignee_id=self.manager.pk)
        notification = Notification.objects.get(user=self.manager)
        self.assertEqual(notification.type, "assigned")
        self.assertIn("WEB-1", notification.message)

    def test_self_assignment_and_unchanged_assignee_do_not_notify(self):
        """No notification for assigning yourself or for edits that keep the assignee."""
        from activity.models import Notification

        self.create_task(self.member, assignee_id=self.member.pk)
        self.client_for(self.manager).patch(
            self.tasks_url(1), {"version": 1, "title": "edited", "assignee_id": self.member.pk}
        )
        self.assertFalse(Notification.objects.exists())

    def test_reassignment_by_another_user_notifies_new_assignee_only(self):
        """Changing the assignee notifies the new person once."""
        from activity.models import Notification

        third = make_user("tara")
        add_member(self.project, third, ProjectMember.Role.MEMBER)
        self.create_task(self.member, assignee_id=self.member.pk)
        self.client_for(self.member).patch(
            self.tasks_url(1), {"version": 1, "assignee_id": third.pk}
        )
        self.assertEqual(
            list(Notification.objects.values_list("user__username", flat=True)), ["tara"]
        )
