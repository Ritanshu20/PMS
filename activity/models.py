from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class ActivityLog(models.Model):
    """Append-only audit row written in the same transaction as the change it describes."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="activity_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    verb = models.CharField(max_length=64)
    target_type = models.CharField(max_length=32)
    target_id = models.BigIntegerField()
    changes = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["project", "-created_at"], name="activity_project_created"),
            # Task history: WHERE project, target_type, target_id ORDER BY created_at DESC.
            models.Index(
                fields=["project", "target_type", "target_id", "-created_at"],
                name="activity_target_created",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.verb} {self.target_type}#{self.target_id}"


class Notification(models.Model):
    class Type(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        MENTIONED = "mentioned", "Mentioned"
        DUE_SOON = "due_soon", "Due soon"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.CharField(max_length=16, choices=Type.choices)
    message = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True)
    task = models.ForeignKey(
        "tasks.Task",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    # Only for due_soon: the local day the reminder belongs to (dedupe key).
    for_date = models.DateField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"], name="notification_user_read")]
        constraints = [
            # RULE + RACE: at most one due_soon reminder per task per user per day,
            # even if notify_due_tasks runs twice at the same moment.
            models.UniqueConstraint(
                fields=["user", "task", "for_date"],
                condition=models.Q(type="due_soon"),
                name="one_due_soon_per_task_user_day",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type} -> {self.user_id}"
