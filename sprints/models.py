from django.db import models
from django.db.models import F, Q


class Sprint(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="sprints"
    )
    name = models.CharField(max_length=100)
    goal = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    # Snapshot taken when the sprint starts (sum of story points, null = 0).
    committed_points = models.PositiveIntegerField(default=0)
    # Snapshot taken when the sprint completes (points of done tasks).
    completed_points = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(start_date__lte=F("end_date")), name="sprint_dates_ordered"
            ),
            # RULE + RACE: at most one active sprint per project, enforced by PostgreSQL
            # (partial unique index) in addition to the lock in sprints.services.start_sprint.
            models.UniqueConstraint(
                fields=["project"],
                condition=Q(status="active"),
                name="one_active_sprint_per_project",
            ),
        ]
        indexes = [models.Index(fields=["project", "status"], name="sprint_project_status")]

    def __str__(self) -> str:
        return self.name


class SprintScopeChange(models.Model):
    """A task added to / removed from a sprint while it was active (used by burndown)."""

    class Kind(models.TextChoices):
        ADDED = "added", "Added"
        REMOVED = "removed", "Removed"

    sprint = models.ForeignKey(Sprint, on_delete=models.CASCADE, related_name="scope_changes")
    # SET_NULL: the history must survive when the task itself is deleted.
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.SET_NULL, null=True, related_name="scope_changes"
    )
    task_number = models.PositiveIntegerField()
    kind = models.CharField(max_length=8, choices=Kind.choices)
    points = models.PositiveIntegerField(default=0)
    at = models.DateTimeField()

    class Meta:
        ordering = ["at", "id"]
        indexes = [models.Index(fields=["sprint", "at"], name="scope_change_sprint_at")]

    def __str__(self) -> str:
        return f"{self.kind} #{self.task_number} ({self.points})"
