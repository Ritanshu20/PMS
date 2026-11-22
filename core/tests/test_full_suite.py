"""
Whole-project safety nets (Phase 12):
- every named URL reverses and resolves,
- an automatic IDOR sweep: an outsider gets 404 on EVERY project endpoint and
  every HTTP method, and object ids from another project never leak.
The sweep walks Django's URL resolver, so new endpoints are covered automatically.
"""

import re
from datetime import date

from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, resolve, reverse

from activity.models import Notification
from core.testing import ProjectWithRolesMixin, make_project
from projects.models import ProjectMember
from sprints.models import Sprint
from tasks.models import Comment, Label, Task, TaskDependency, TimeEntry

HTTP_METHODS = ("get", "post", "patch", "delete")
PARAM = re.compile(r"<(?:\w+:)?(\w+)>")
# One body that passes input validation on every write endpoint, so the request
# really reaches the object lookup (serializers ignore the keys they don't use).
VALID_BODY = {
    "role": "viewer",
    "body": "hack",
    "unfinished_action": "backlog",
    "minutes": 5,
    "name": "x",
}


def walk_patterns(patterns, prefix: str = ""):
    """Yield (route, view_class, name) for every API URL pattern."""
    for entry in patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            yield from walk_patterns(entry.url_patterns, route)
        elif isinstance(entry, URLPattern) and route.startswith("api/"):
            view_class = getattr(entry.callback, "view_class", None)
            if view_class is not None:
                yield route, view_class, entry.name


def build_url(route: str, values: dict[str, int]) -> str:
    return "/" + PARAM.sub(lambda match: str(values[match.group(1)]), route)


class UrlResolutionTests(TestCase):
    def test_every_named_api_url_reverses_and_resolves(self):
        """No broken routes: reverse(name) -> URL -> resolve() gives the same view."""
        sample = {name: 1 for name in ("project_id", "number", "sprint_id", "label_id")}
        sample.update(comment_id=1, entry_id=1, member_id=1, blocker_number=2, notification_id=1)
        checked = 0
        for route, view_class, name in walk_patterns(get_resolver().url_patterns):
            if not name:
                continue
            kwargs = {key: sample[key] for key in PARAM.findall(route)}
            url = reverse(name, kwargs=kwargs)
            self.assertEqual(resolve(url).func.view_class, view_class, name)
            checked += 1
        self.assertGreater(checked, 40)


class IdorSweepTests(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = Task.objects.create(
            project=self.project, number=1, title="secret", reporter=self.owner
        )
        blocker = Task.objects.create(
            project=self.project, number=2, title="b", reporter=self.owner
        )
        TaskDependency.objects.create(project=self.project, blocker=blocker, blocked=self.task)
        self.sprint = Sprint.objects.create(
            project=self.project, name="S", start_date=date(2026, 3, 2), end_date=date(2026, 3, 6)
        )
        self.label = Label.objects.create(project=self.project, name="l")
        self.comment = Comment.objects.create(task=self.task, author=self.member, body="hi")
        self.entry = TimeEntry.objects.create(
            task=self.task, user=self.member, minutes=10, date=date(2026, 3, 2)
        )
        self.notification = Notification.objects.create(
            user=self.member, project=self.project, type="assigned"
        )
        self.values = {
            "project_id": self.project.pk,
            "number": 1,
            "blocker_number": 2,
            "sprint_id": self.sprint.pk,
            "label_id": self.label.pk,
            "comment_id": self.comment.pk,
            "entry_id": self.entry.pk,
            "member_id": ProjectMember.objects.get(user=self.member).pk,
            "notification_id": self.notification.pk,
        }

    def project_endpoints(self):
        for route, view_class, _ in walk_patterns(get_resolver().url_patterns):
            if "project_id" in route:
                for method in HTTP_METHODS:
                    if hasattr(view_class, method):
                        yield method, route

    def test_outsider_gets_404_on_every_project_endpoint_and_method(self):
        """User B can never read or change anything inside user A's project."""
        client = self.client_for(self.outsider)
        swept = 0
        for method, route in self.project_endpoints():
            url = build_url(route, self.values)
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(client, method)(url, VALID_BODY).status_code, 404)
            swept += 1
        self.assertGreater(swept, 45)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "secret")

    def test_object_ids_from_another_project_are_404_inside_my_project(self):
        """B uses A's comment/entry/label/sprint/member ids under B's own project."""
        own_project = make_project(self.outsider, key="OWN")
        Task.objects.create(project=own_project, number=1, title="mine", reporter=self.outsider)
        Task.objects.create(project=own_project, number=2, title="mine2", reporter=self.outsider)
        values = dict(self.values, project_id=own_project.pk)
        client = self.client_for(self.outsider)
        object_params = ("sprint_id", "label_id", "comment_id", "entry_id", "member_id")
        for method, route in self.project_endpoints():
            if not any(f"<int:{param}>" in route for param in object_params):
                continue
            if method == "post" and "complete" not in route and "start" not in route:
                continue
            url = build_url(route, values)
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(client, method)(url, VALID_BODY).status_code, 404)

    def test_notification_of_another_user_is_404(self):
        """Notifications are scoped to their owner."""
        url = f"/api/notifications/{self.notification.pk}/read/"
        self.assertEqual(self.client_for(self.outsider).post(url).status_code, 404)
        self.notification.refresh_from_db()
        self.assertIsNone(self.notification.read_at)
