from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower


class Label(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="labels")
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=7, default="#6c757d")

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint("project", Lower("name"), name="unique_label_name_per_project"),
            models.CheckConstraint(
                condition=Q(color__regex=r"^#[0-9A-Fa-f]{6}$"), name="label_color_hex"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Task(models.Model):
    class Status(models.TextChoices):
        BACKLOG = "backlog", "Backlog"
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        IN_REVIEW = "in_review", "In review"
        DONE = "done", "Done"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="tasks")
    # RACE: assigned from Project.task_counter while the project row is locked.
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.BACKLOG)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.MEDIUM)
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="reported_tasks",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    due_date = models.DateField(null=True, blank=True)
    story_points = models.PositiveSmallIntegerField(null=True, blank=True)
    # Optional time estimate used by the "estimate vs logged" report.
    estimate_minutes = models.PositiveIntegerField(null=True, blank=True)
    sprint = models.ForeignKey(
        "sprints.Sprint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    position = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    # RACE: optimistic-locking counter; every successful update does version = version + 1.
    version = models.PositiveIntegerField(default=1)
    labels = models.ManyToManyField(Label, blank=True, related_name="tasks")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="unique_task_number"),
            # RULE: completed_at is set exactly when the task is done.
            models.CheckConstraint(
                condition=(Q(status="done") & Q(completed_at__isnull=False))
                | (~Q(status="done") & Q(completed_at__isnull=True)),
                name="task_completed_at_matches_status",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status", "position"], name="task_board_order"),
            models.Index(fields=["project", "assignee"], name="task_project_assignee"),
            models.Index(fields=["project", "due_date"], name="task_project_due"),
        ]

    def __str__(self) -> str:
        return f"#{self.number} {self.title}"


class Comment(models.Model):
    """A comment on a task. Deleting is soft: the row stays and shows "[deleted]"."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="comments"
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["task", "created_at"], name="comment_task_created")]

    def __str__(self) -> str:
        return f"comment {self.pk} on task {self.task_id}"


class TimeEntry(models.Model):
    """Minutes a user worked on a task on one day."""

    MIN_MINUTES = 1
    MAX_MINUTES = 1440  # RULE: one entry can never exceed a full day

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="time_entries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="time_entries"
    )
    minutes = models.PositiveSmallIntegerField()
    date = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(minutes__gte=1) & Q(minutes__lte=1440),
                name="time_entry_minutes_range",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "date"], name="time_entry_user_date"),
            models.Index(fields=["task", "date"], name="time_entry_task_date"),
        ]

    def __str__(self) -> str:
        return f"{self.minutes} min on task {self.task_id}"


class TaskDependency(models.Model):
    """`blocker` must be done before `blocked` can be done. Both are in `project`."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="dependencies"
    )
    blocker = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="blocking_links")
    blocked = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="blocked_by_links")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked"], name="unique_dependency_pair"),
            models.CheckConstraint(condition=~Q(blocker=F("blocked")), name="dependency_not_self"),
        ]

    def __str__(self) -> str:
        return f"{self.blocker_id} blocks {self.blocked_id}"
