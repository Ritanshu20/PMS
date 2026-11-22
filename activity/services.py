"""
Write helpers for the activity log and in-app notifications.

`log_activity` and `notify` are called by other apps' services *inside* their
`transaction.atomic()` block, so the log/notification row commits or rolls back
together with the change. Never call them from views directly.
`create_due_soon_notifications` is the job behind `manage.py notify_due_tasks`.
"""

from datetime import timedelta
from typing import Any

from django.db import models, transaction
from django.http import Http404
from django.utils import timezone

from activity.models import ActivityLog, Notification
from core import clock
from projects.models import Project

DUE_SOON_WINDOW = timedelta(hours=24)


def log_activity(
    *, project, actor, verb: str, target: models.Model, changes: dict[str, Any] | None = None
) -> ActivityLog:
    """Record one state change. `target_type` is the model name, e.g. "task"."""
    return ActivityLog.objects.create(
        project=project,
        actor=actor,
        verb=verb,
        target_type=target._meta.model_name,
        target_id=target.pk,
        changes=changes or {},
    )


def notify(*, user, project, type: str, message: str, link: str = "", task=None) -> Notification:
    """Create one in-app notification for `user`."""
    return Notification.objects.create(
        user=user, project=project, type=type, message=message[:255], link=link, task=task
    )


def mark_notification_read(*, user, notification_id: int) -> Notification:
    """Mark one of the user's own notifications as read (idempotent).

    Raises:
        Http404: the notification belongs to someone else or does not exist.
    """
    # SAFETY: scoped to the requesting user (no IDOR).
    notification = Notification.objects.filter(user=user, pk=notification_id).first()
    if notification is None:
        raise Http404("Notification not found.")
    if notification.read_at is None:
        notification.read_at = clock.now()
        notification.save(update_fields=["read_at"])
    return notification


def mark_all_notifications_read(*, user) -> int:
    """Mark every unread notification of the user as read; returns how many changed."""
    return Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=clock.now())


def create_due_soon_notifications() -> int:
    """Remind assignees about open tasks due within the next 24 hours.

    Why: people should hear about deadlines before they are missed.

    Rule: an open (not done) task with an assignee in an active project whose
    due date is between today and the local date 24 hours from now (inclusive).
    At most one reminder per task per user per day.

    How it stays idempotent: `get_or_create` on (user, task, for_date) backed by
    the partial unique constraint `one_due_soon_per_task_user_day`, so running
    the command twice, even at the same moment, creates no duplicates.

    Example (now = 10 Mar 15:00 IST): due 10 Mar or 11 Mar -> reminded;
    due 9 Mar (overdue) or 12 Mar -> not reminded.

    Returns:
        Number of notifications created by this run.
    """
    from tasks.models import Task

    now = clock.now()
    today = timezone.localdate(now)
    last_day = timezone.localdate(now + DUE_SOON_WINDOW)
    tasks = (
        Task.objects.filter(
            due_date__gte=today,
            due_date__lte=last_day,
            assignee__isnull=False,
            project__status=Project.Status.ACTIVE,
        )
        .exclude(status=Task.Status.DONE)
        .select_related("project", "assignee")
    )
    created_count = 0
    for task in tasks:
        created_count += _create_due_soon(task, today)
    return created_count


def _create_due_soon(task, today) -> int:
    project = task.project
    # RACE: get_or_create + the partial unique constraint make concurrent runs safe.
    with transaction.atomic():
        _, created = Notification.objects.get_or_create(
            user=task.assignee,
            task=task,
            type=Notification.Type.DUE_SOON,
            for_date=today,
            defaults={
                "project": project,
                "message": f"{project.key}-{task.number} '{task.title}' is due on "
                f"{task.due_date.isoformat()}"[:255],
                "link": f"/projects/{project.pk}/tasks/{task.number}/",
            },
        )
    return 1 if created else 0
