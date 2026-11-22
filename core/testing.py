"""
Shared test helpers: tiny factories and the concurrency runner.

Only imported by tests. Kept outside `tests/` packages so every app can reuse it.
"""

import threading
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.db import connection
from rest_framework.test import APIClient

from accounts.models import User
from projects.models import Project, ProjectMember

DEFAULT_PASSWORD = "Str0ng-Test-Pass!"


def make_user(username: str, email: str | None = None) -> User:
    return User.objects.create_user(
        username=username, email=email or f"{username}@example.com", password=DEFAULT_PASSWORD
    )


def make_project(owner: User, key: str = "WEB", name: str = "Website") -> Project:
    project = Project.objects.create(name=name, key=key)
    ProjectMember.objects.create(project=project, user=owner, role=ProjectMember.Role.OWNER)
    return project


def add_member(project: Project, user: User, role: str) -> ProjectMember:
    return ProjectMember.objects.create(project=project, user=user, role=role)


def membership_of(user: User, project: Project) -> ProjectMember:
    return ProjectMember.objects.select_related("project", "user").get(project=project, user=user)


def api_client_for(user: User | None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


class ProjectWithRolesMixin:
    """Creates project WEB with one user per role, plus an outsider, in setUp."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.owner = make_user("olivia")
        self.manager = make_user("manoj")
        self.member = make_user("meera")
        self.viewer = make_user("vikram")
        self.outsider = make_user("oscar")
        self.project = make_project(self.owner)
        add_member(self.project, self.manager, ProjectMember.Role.MANAGER)
        add_member(self.project, self.member, ProjectMember.Role.MEMBER)
        add_member(self.project, self.viewer, ProjectMember.Role.VIEWER)
        self.users_by_role = {
            "owner": self.owner,
            "manager": self.manager,
            "member": self.member,
            "viewer": self.viewer,
        }

    def client_for(self, user: User | None) -> APIClient:
        return api_client_for(user)


def run_concurrently(functions: list[Callable[[], Any]]) -> list[tuple[str, Any]]:
    """Run each function in its own thread, all released at the same instant.

    Returns one ("ok", result) or ("error", exception) per function, in order.
    Each thread closes its own DB connection so PostgreSQL sessions are not leaked.
    """
    barrier = threading.Barrier(len(functions))
    outcomes: list[tuple[str, Any]] = [("error", None)] * len(functions)

    def worker(index: int, function: Callable[[], Any]) -> None:
        try:
            barrier.wait(timeout=30)
            outcomes[index] = ("ok", function())
        except Exception as exc:  # noqa: BLE001 - the test inspects every outcome
            outcomes[index] = ("error", exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(index, function))
        for index, function in enumerate(functions)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return outcomes
