"""
`manage.py seed_demo`: create a demo project for screenshots and the demo script.

Idempotent: running it twice creates nothing new. Users and the project are
fetched or created; tasks, sprints, comments and time are added only when the
demo project has no tasks yet. LOCAL DEMO ONLY: the demo password is public.
"""

import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from core import clock
from projects.models import Project, ProjectMember
from sprints import services as sprint_services
from sprints.models import Sprint
from tasks import comment_services, dependency_services, services, time_services
from tasks.models import Label, Task

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-pass-2026")  # noqa: S105 - local demo only
DEMO_KEY = "DEMO"
DEMO_USERS = [
    # username, role, display name
    ("demo_owner", ProjectMember.Role.OWNER, "Olivia Owner"),
    ("demo_manager", ProjectMember.Role.MANAGER, "Manoj Manager"),
    ("demo_member", ProjectMember.Role.MEMBER, "Meera Member"),
    ("demo_viewer", ProjectMember.Role.VIEWER, "Vikram Viewer"),
]
DEMO_TASKS = [
    # title, priority, points, assignee username
    ("Design login page", "high", 3, "demo_member"),
    ("Build login API", "urgent", 5, "demo_member"),
    ("Connect login page to API", "high", 3, "demo_manager"),
    ("Write onboarding docs", "low", 2, "demo_member"),
    ("Set up CI pipeline", "medium", 5, "demo_manager"),
    ("Add password reset", "medium", 3, None),
    ("Dark mode", "low", None, None),
    ("Performance audit", "medium", 8, None),
]


class Command(BaseCommand):
    help = "Create (or keep) the demo project with users for every role. Idempotent."

    def handle(self, *args, **options) -> None:
        with transaction.atomic():
            users = self._users()
            project = self._project(users)
            if project.tasks.exists():
                self.stdout.write("Demo data already present; nothing created.")
            else:
                self._content(project, users)
                self.stdout.write(self.style.SUCCESS("Demo data created."))
        self.stdout.write(
            f"Log in as demo_owner / demo_manager / demo_member / demo_viewer, "
            f"password '{DEMO_PASSWORD}'."
        )

    def _users(self) -> dict[str, User]:
        users = {}
        for username, _, display_name in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@example.com", "display_name": display_name},
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
            users[username] = user
        return users

    def _project(self, users: dict[str, User]) -> Project:
        project, _ = Project.objects.get_or_create(
            key=DEMO_KEY,
            defaults={"name": "Demo Website", "description": "Seeded by manage.py seed_demo."},
        )
        for username, role, _ in DEMO_USERS:
            ProjectMember.objects.get_or_create(
                project=project, user=users[username], defaults={"role": role}
            )
        return project

    def _content(self, project: Project, users: dict[str, User]) -> None:
        owner = ProjectMember.objects.select_related("project", "user").get(
            project=project, user=users["demo_owner"]
        )
        bug = Label.objects.create(project=project, name="bug", color="#dc3545")
        Label.objects.create(project=project, name="frontend", color="#0d6efd")
        tasks = self._tasks(owner, users, bug)
        # Dependency chain: DEMO-1 blocks DEMO-2 blocks DEMO-3.
        dependency_services.add_dependency(membership=owner, blocker_number=1, blocked_number=2)
        dependency_services.add_dependency(membership=owner, blocker_number=2, blocked_number=3)
        self._sprints(owner, tasks)
        self._talk_and_time(project, users, tasks)

    def _tasks(self, owner: ProjectMember, users: dict[str, User], bug: Label) -> list[Task]:
        created = []
        for title, priority, points, assignee in DEMO_TASKS:
            created.append(
                services.create_task(
                    membership=owner,
                    title=title,
                    priority=priority,
                    story_points=points,
                    status=Task.Status.TODO,
                    assignee_id=users[assignee].pk if assignee else None,
                    estimate_minutes=points * 60 if points else None,
                    label_ids=[bug.pk] if title == "Build login API" else None,
                )
            )
        return created

    def _sprints(self, owner: ProjectMember, tasks: list[Task]) -> None:
        today = clock.today()
        old = sprint_services.create_sprint(
            membership=owner,
            name="Sprint 0",
            start_date=today - timedelta(days=21),
            end_date=today - timedelta(days=8),
        )
        Sprint.objects.filter(pk=old.pk).update(
            status=Sprint.Status.COMPLETED,
            committed_points=10,
            completed_points=8,
            completed_at=clock.now() - timedelta(days=8),
        )
        sprint = sprint_services.create_sprint(
            membership=owner,
            name="Sprint 1",
            goal="Users can log in",
            start_date=today - timedelta(days=4),
            end_date=today + timedelta(days=5),
        )
        for task in tasks[:5]:
            sprint_services.add_task_to_sprint(
                membership=owner, sprint_id=sprint.pk, task_number=task.number
            )
        sprint_services.start_sprint(membership=owner, sprint_id=sprint.pk)
        now = clock.now()
        # Backdated progress so the burndown has a shape: DEMO-1 done 3 days ago,
        # DEMO-4 done yesterday, DEMO-2 in progress, DEMO-5 in review.
        Task.objects.filter(pk=tasks[0].pk).update(
            status="done", completed_at=now - timedelta(days=3)
        )
        Task.objects.filter(pk=tasks[3].pk).update(
            status="done", completed_at=now - timedelta(days=1)
        )
        Task.objects.filter(pk=tasks[1].pk).update(status="in_progress")
        Task.objects.filter(pk=tasks[4].pk).update(status="in_review")
        Task.objects.filter(pk__in=[task.pk for task in tasks[5:]]).update(status="backlog")

    def _talk_and_time(self, project: Project, users: dict[str, User], tasks: list[Task]) -> None:
        member = ProjectMember.objects.select_related("project", "user").get(
            project=project, user=users["demo_member"]
        )
        comment_services.create_comment(
            membership=member,
            task_number=tasks[1].number,
            body="API is almost ready. @demo_manager can you review the error format?",
        )
        yesterday = clock.today() - timedelta(days=1)
        time_services.log_time(
            membership=member, task_number=tasks[1].number, minutes=180, date=yesterday
        )
        time_services.log_time(
            membership=member, task_number=tasks[0].number, minutes=150, date=yesterday
        )
