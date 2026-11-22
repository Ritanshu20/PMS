"""
Read queries for notifications and the activity feed.

Notifications are always scoped to the requesting user; the activity feed is
scoped to one project whose membership the caller has already proven.
"""

from django.db.models import QuerySet

from activity.models import ActivityLog, Notification
from projects.models import Project
from projects.permissions import visible_projects_filter


def notifications_for_user(user, unread_only: bool = False) -> QuerySet[Notification]:
    """The user's notifications, newest first.

    SAFETY: only projects the user is still a member of, so a removed member
    no longer sees links into that project.
    """
    my_projects = Project.objects.filter(visible_projects_filter(user)).values("pk")
    queryset = Notification.objects.filter(user=user, project_id__in=my_projects)
    if unread_only:
        queryset = queryset.filter(read_at__isnull=True)
    return queryset.select_related("project").order_by("-created_at", "-id")


def unread_notification_count(user) -> int:
    return notifications_for_user(user, unread_only=True).count()


def task_activity(task) -> QuerySet[ActivityLog]:
    """History of one task (edits, moves, comments, dependencies, time), newest first."""
    return (
        ActivityLog.objects.filter(
            project_id=task.project_id, target_type="task", target_id=task.pk
        )
        .select_related("actor")
        .order_by("-created_at", "-id")
    )


def project_activity(project_id: int) -> QuerySet[ActivityLog]:
    """Activity feed of one project, newest first, with the actor loaded."""
    return (
        ActivityLog.objects.filter(project_id=project_id)
        .select_related("actor")
        .order_by("-created_at", "-id")
    )
