from django.conf import settings
from django.db import models
from django.db.models import Q


class Project(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=120)
    # RULE: 2-6 uppercase letters, globally unique, e.g. "WEB" -> task keys "WEB-12".
    key = models.CharField(max_length=6, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    # RACE: incremented only while the project row is locked (see tasks.services.create_task).
    task_counter = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(key__regex=r"^[A-Z]{2,6}$"), name="project_key_format"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key} - {self.name}"

    @property
    def is_archived(self) -> bool:
        return self.status == self.Status.ARCHIVED


class ProjectMember(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="unique_project_member"),
        ]
        indexes = [models.Index(fields=["user", "project"], name="member_user_project")]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.project_id} ({self.role})"
