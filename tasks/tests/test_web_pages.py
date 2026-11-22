"""
Template (server-rendered) pages: access by role, CSRF, board moves, forms.
These use Django's test Client with session login, like a real browser.
"""

import json

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from activity.models import Notification
from core.testing import DEFAULT_PASSWORD, ProjectWithRolesMixin, make_project
from core.web import VERSION_CONFLICT_MESSAGE
from projects.models import Project
from tasks.models import Comment, Task, TaskDependency


class WebTestCase(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = Task.objects.create(
            project=self.project,
            number=1,
            title="Login page",
            status="todo",
            reporter=self.owner,
            assignee=self.member,
        )
        # Keep the counter in sync because the task above bypassed create_task.
        Project.objects.filter(pk=self.project.pk).update(task_counter=1)
        self.board_url = f"/projects/{self.project.pk}/board/"
        self.detail_url = f"/projects/{self.project.pk}/tasks/1/"

    def browser(self, user=None, csrf_checks: bool = False) -> Client:
        client = Client(enforce_csrf_checks=csrf_checks)
        if user is not None:
            client.force_login(user)
        return client

    def move(self, client: Client, number: int, status: str, version: int, position: int = 0):
        return client.post(
            f"/projects/{self.project.pk}/tasks/{number}/move/",
            data=json.dumps({"status": status, "position": position, "version": version}),
            content_type="application/json",
        )


class AuthPageTests(WebTestCase):
    def test_anonymous_user_is_redirected_to_login(self):
        """Every page needs a session login."""
        response = self.browser().get(self.board_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_register_page_creates_user_and_logs_in(self):
        """The register form uses the same service as the API."""
        client = self.browser()
        response = client.post(
            "/accounts/register/",
            {
                "username": "newbie",
                "email": "newbie@example.com",
                "password": DEFAULT_PASSWORD,
                "password_confirm": DEFAULT_PASSWORD,
            },
        )
        self.assertRedirects(response, "/projects/")
        self.assertTrue(User.objects.filter(username="newbie").exists())

    def test_register_page_shows_service_errors_on_the_form(self):
        """Duplicate email is reported next to the field."""
        response = self.browser().post(
            "/accounts/register/",
            {
                "username": "other",
                "email": self.member.email,
                "password": DEFAULT_PASSWORD,
                "password_confirm": DEFAULT_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")

    def test_login_and_logout(self):
        """Session login with the login form; logout is POST only."""
        client = self.browser()
        response = client.post(
            "/accounts/login/", {"username": "meera", "password": DEFAULT_PASSWORD}
        )
        self.assertRedirects(response, "/projects/")
        self.assertEqual(client.post("/accounts/logout/").status_code, 302)
        self.assertEqual(client.get(self.board_url).status_code, 302)


class ProjectPageTests(WebTestCase):
    def test_project_list_shows_only_my_projects(self):
        """No global listing in the UI either."""
        make_project(self.outsider, key="HIDE", name="Hidden project")
        response = self.browser(self.member).get("/projects/")
        self.assertContains(response, "Website")
        self.assertNotContains(response, "Hidden project")

    def test_create_project_page(self):
        """The creator lands on the new board as owner."""
        response = self.browser(self.outsider).post(
            "/projects/new/", {"name": "Mobile", "key": "app", "description": ""}
        )
        project = Project.objects.get(key="APP")
        self.assertRedirects(response, f"/projects/{project.pk}/board/")

    def test_invalid_key_shows_message_and_stays_on_form(self):
        """Service validation errors become flash messages."""
        response = self.browser(self.outsider).post(
            "/projects/new/", {"name": "X", "key": "W3B"}, follow=True
        )
        self.assertContains(response, "Key must be 2 to 6 letters")


class BoardPageTests(WebTestCase):
    def test_board_access_by_role(self):
        """Members see draggable cards; viewers see a read-only board; outsiders 404."""
        member_page = self.browser(self.member).get(self.board_url)
        self.assertContains(member_page, 'draggable="true"')
        self.assertContains(member_page, "New task")
        viewer_page = self.browser(self.viewer).get(self.board_url)
        self.assertEqual(viewer_page.status_code, 200)
        self.assertNotContains(viewer_page, 'draggable="true"')
        self.assertNotContains(viewer_page, "New task")
        self.assertEqual(self.browser(self.outsider).get(self.board_url).status_code, 404)

    def test_board_move_success_returns_new_version(self):
        """A valid drop saves and returns the next version."""
        response = self.move(self.browser(self.member), 1, "in_progress", version=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 2)
        self.assertEqual(Task.objects.get(pk=self.task.pk).status, "in_progress")

    def test_rejected_move_explains_why(self):
        """Skipping a step is refused with a readable message."""
        response = self.move(self.browser(self.member), 1, "done", version=1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_transition")

    def test_move_blocked_by_open_task_names_the_blocker(self):
        """'Blocked by WEB-2' is shown when a blocker is still open."""
        blocker = Task.objects.create(
            project=self.project, number=2, title="b", reporter=self.owner
        )
        TaskDependency.objects.create(project=self.project, blocker=blocker, blocked=self.task)
        Task.objects.filter(pk=self.task.pk).update(status="in_review")
        response = self.move(self.browser(self.member), 1, "done", version=1)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Blocked by WEB-2", response.json()["detail"])

    def test_stale_card_gets_version_conflict(self):
        """Two tabs: the second drop with the old version gets 409."""
        client = self.browser(self.member)
        self.move(client, 1, "backlog", version=1)
        response = self.move(client, 1, "todo", version=1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], VERSION_CONFLICT_MESSAGE)

    def test_viewer_move_is_forbidden(self):
        """The JSON endpoint enforces the same permission module."""
        response = self.move(self.browser(self.viewer), 1, "in_progress", version=1)
        self.assertEqual(response.status_code, 403)

    def test_board_query_count(self):
        """Fixed number of queries: 1 task or 21 tasks cost the same."""
        client = self.browser(self.member)
        with CaptureQueriesContext(connection) as one_task:
            client.get(self.board_url)
        for number in range(2, 22):
            Task.objects.create(
                project=self.project,
                number=number,
                title="x",
                reporter=self.owner,
                assignee=self.member,
            )
        with self.assertNumQueries(len(one_task)):
            client.get(self.board_url)


class CsrfTests(WebTestCase):
    def test_post_without_csrf_token_is_rejected(self):
        """Session-authenticated POSTs require the CSRF token."""
        client = self.browser(self.member, csrf_checks=True)
        response = client.post(f"{self.detail_url}comments/", {"body": "hi"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Comment.objects.exists())

    def test_board_move_without_csrf_token_is_rejected(self):
        """The drag-and-drop endpoint is CSRF protected too."""
        client = self.browser(self.member, csrf_checks=True)
        self.assertEqual(self.move(client, 1, "in_progress", version=1).status_code, 403)


class TaskPageTests(WebTestCase):
    def test_edit_with_stale_version_shows_conflict_message(self):
        """The hidden version field turns a lost update into a clear message."""
        Task.objects.filter(pk=self.task.pk).update(version=2, title="Changed elsewhere")
        response = self.browser(self.member).post(
            f"{self.detail_url}edit/",
            {"title": "Mine", "priority": "medium", "status": "todo", "version": 1},
            follow=True,
        )
        self.assertContains(response, "Someone else changed this task")
        self.assertContains(response, "Changed elsewhere")
        self.assertEqual(Task.objects.get(pk=self.task.pk).title, "Changed elsewhere")

    def test_edit_with_current_version_saves(self):
        """Normal edit path."""
        response = self.browser(self.member).post(
            f"{self.detail_url}edit/",
            {"title": "Mine", "priority": "high", "status": "todo", "version": 1},
        )
        self.assertRedirects(response, self.detail_url)
        self.assertEqual(Task.objects.get(pk=self.task.pk).title, "Mine")

    def test_create_task_page(self):
        """New task form creates WEB-2."""
        response = self.browser(self.member).post(
            f"/projects/{self.project.pk}/tasks/new/",
            {"title": "From page", "priority": "medium", "status": "backlog"},
        )
        self.assertRedirects(response, f"/projects/{self.project.pk}/tasks/2/")

    def test_viewer_cannot_open_create_or_edit_pages(self):
        """Pages enforce the same matrix as the API."""
        viewer = self.browser(self.viewer)
        self.assertEqual(viewer.get(f"/projects/{self.project.pk}/tasks/new/").status_code, 403)
        self.assertEqual(viewer.get(f"{self.detail_url}edit/").status_code, 403)

    def test_comment_with_mention_notifies_and_is_escaped(self):
        """Mention flow through the page; HTML in comments is shown as text."""
        client = self.browser(self.member)
        client.post(f"{self.detail_url}comments/", {"body": "@manoj <script>alert(1)</script>"})
        self.assertTrue(Notification.objects.filter(user=self.manager, type="mentioned").exists())
        page = client.get(self.detail_url)
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(page, "<script>alert(1)</script>")

    def test_dependency_add_and_rule_message(self):
        """Adding a self-dependency shows the service's message."""
        response = self.browser(self.member).post(
            f"{self.detail_url}dependencies/", {"blocker_number": 1}, follow=True
        )
        self.assertContains(response, "A task cannot block itself.")

    def test_task_detail_query_count(self):
        """The detail page has a fixed number of queries regardless of comments."""
        client = self.browser(self.member)
        Comment.objects.create(task=self.task, author=self.member, body="one")
        with CaptureQueriesContext(connection) as few:
            client.get(self.detail_url)
        for index in range(10):
            Comment.objects.create(task=self.task, author=self.manager, body=f"c{index}")
        with self.assertNumQueries(len(few)):
            client.get(self.detail_url)

    def test_outsider_gets_404_on_task_pages(self):
        """Non-members get 404 for every page of the project."""
        outsider = self.browser(self.outsider)
        for url in (
            self.detail_url,
            f"{self.detail_url}edit/",
            f"/projects/{self.project.pk}/tasks/new/",
        ):
            with self.subTest(url=url):
                self.assertEqual(outsider.get(url).status_code, 404)
        self.assertEqual(
            outsider.post(f"{self.detail_url}comments/", {"body": "x"}).status_code, 404
        )
