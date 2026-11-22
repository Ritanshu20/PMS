"""
Read-only reports: logged time and workload. All aggregation runs in PostgreSQL.

Callers must have proven project membership first; every query is scoped by
`project_id`. Percentages never divide by zero (missing estimate -> None).
"""

from datetime import date
from typing import Any

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce

from projects.models import ProjectMember
from projects.permissions import ASSIGNABLE_ROLES
from tasks.models import Task, TimeEntry


def time_by_task(project_id: int, sprint_id: int | None = None) -> list[dict[str, Any]]:
    """Logged minutes per task, compared with the estimate where one exists.

    Example: estimate 120, logged 150 -> percent_of_estimate 125.0, over_estimate True.
    """
    tasks = Task.objects.filter(project_id=project_id)
    if sprint_id is not None:
        tasks = tasks.filter(sprint_id=sprint_id)
    rows = (
        tasks.annotate(logged_minutes=Coalesce(Sum("time_entries__minutes"), 0))
        .filter(Q(logged_minutes__gt=0) | Q(estimate_minutes__isnull=False))
        .values("number", "title", "estimate_minutes", "logged_minutes")
        .order_by("number")
    )
    return [_with_estimate_comparison(row) for row in rows]


def _with_estimate_comparison(row: dict[str, Any]) -> dict[str, Any]:
    estimate = row["estimate_minutes"]
    logged = row["logged_minutes"]
    has_estimate = bool(estimate)
    row["percent_of_estimate"] = round(logged * 100 / estimate, 1) if has_estimate else None
    row["over_estimate"] = has_estimate and logged > estimate
    return row


def time_by_user(
    project_id: int, date_from: date | None = None, date_to: date | None = None
) -> list[dict[str, Any]]:
    """Total logged minutes per user in this project, optionally within a date range."""
    entries = TimeEntry.objects.filter(task__project_id=project_id)
    if date_from:
        entries = entries.filter(date__gte=date_from)
    if date_to:
        entries = entries.filter(date__lte=date_to)
    rows = (
        entries.values("user_id", "user__username")
        .annotate(minutes=Sum("minutes"), entries=Count("id"))
        .order_by("-minutes", "user__username")
    )
    return [
        {
            "user_id": row["user_id"],
            "username": row["user__username"],
            "minutes": row["minutes"],
            "entries": row["entries"],
        }
        for row in rows
    ]


def time_by_sprint(project_id: int) -> list[dict[str, Any]]:
    """Logged minutes per sprint (time on tasks currently planned into that sprint)."""
    rows = (
        TimeEntry.objects.filter(task__project_id=project_id, task__sprint__isnull=False)
        .values("task__sprint_id", "task__sprint__name")
        .annotate(minutes=Sum("minutes"))
        .order_by("task__sprint__start_date", "task__sprint_id")
    )
    return [
        {
            "sprint_id": row["task__sprint_id"],
            "name": row["task__sprint__name"],
            "minutes": row["minutes"],
        }
        for row in rows
    ]


def workload(project_id: int) -> list[dict[str, Any]]:
    """Open tasks and open story points per assignable member (members with 0 included).

    Example: Meera has 2 open tasks of 3 and 5 points -> open_tasks 2, open_points 8.
    """
    open_work = {
        row["assignee_id"]: row
        for row in Task.objects.filter(project_id=project_id, assignee__isnull=False)
        .exclude(status=Task.Status.DONE)
        .values("assignee_id")
        .annotate(open_tasks=Count("id"), open_points=Coalesce(Sum("story_points"), 0))
        .order_by()
    }
    members = (
        ProjectMember.objects.filter(project_id=project_id, role__in=ASSIGNABLE_ROLES)
        .select_related("user")
        .order_by("user__username")
    )
    return [
        {
            "user_id": member.user_id,
            "username": member.user.username,
            "role": member.role,
            "open_tasks": open_work.get(member.user_id, {}).get("open_tasks", 0),
            "open_points": open_work.get(member.user_id, {}).get("open_points", 0),
        }
        for member in members
    ]
