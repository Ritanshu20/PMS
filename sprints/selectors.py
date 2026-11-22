"""
Read queries for sprints.

Callers must have proven membership with `projects.permissions.get_membership_or_404`;
every query is scoped by `project_id`.
"""

from datetime import date, timedelta
from typing import Any

from django.db.models import Count, QuerySet, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.http import Http404
from django.utils import timezone

from core import clock
from core.exceptions import BusinessRuleError
from sprints.models import Sprint, SprintScopeChange
from tasks.models import Task

VELOCITY_WINDOW = 3  # RULE: average velocity = mean of the last 3 completed sprints
VELOCITY_HISTORY = 10  # how many completed sprints the velocity chart shows


def sprints_for_project(project_id: int, status: str | None = None) -> QuerySet[Sprint]:
    """Sprints of a project with task count and current planned points (one query)."""
    queryset = Sprint.objects.filter(project_id=project_id)
    if status:
        queryset = queryset.filter(status=status)
    return queryset.annotate(
        task_count=Count("tasks"),
        planned_points=Coalesce(Sum("tasks__story_points"), 0),
    ).order_by("start_date", "id")


def get_sprint_or_404(project_id: int, sprint_id: int) -> Sprint:
    sprint = sprints_for_project(project_id).filter(pk=sprint_id).first()
    if sprint is None:
        raise Http404("Sprint not found.")
    return sprint


def active_sprint(project_id: int) -> Sprint | None:
    return sprints_for_project(project_id, Sprint.Status.ACTIVE).first()


def burndown(sprint: Sprint) -> dict[str, Any]:
    """Actual and ideal remaining points for every day of a started sprint.

    Why: shows whether the team will finish the committed work in time.

    Formula (day D from start_date to min(today, end_date), project timezone):
        remaining(D) = committed_points
                       + points added by D
                       - points removed by D
                       - points of sprint tasks completed on or before D
        ideal(D) = committed_points * (end_date - D) / (end_date - start_date)
    "by D" means the event's local date (Asia/Kolkata) is <= D. Days after
    today have `remaining = None` (future), but still have an ideal value.
    A single-day sprint has ideal 0 (the target is reached on the end date).

    Example (committed 10, Mon-Fri, 3 pts done Tue, 5 pts done Thu):
        remaining = [10, 7, 7, 2, 2], ideal = [10, 7.5, 5, 2.5, 0]

    Raises:
        BusinessRuleError: the sprint has not started yet.
    """
    if sprint.status == Sprint.Status.PLANNED:
        raise BusinessRuleError("This sprint has not started yet.", code="sprint_not_started")
    added, removed, completed = _burndown_events(sprint)
    last_actual_day = min(clock.today(), sprint.end_date)
    running = {
        "added": _sum_before(added, sprint.start_date),
        "removed": _sum_before(removed, sprint.start_date),
        "completed": _sum_before(completed, sprint.start_date),
    }
    days = []
    for day in _date_range(sprint.start_date, sprint.end_date):
        running["added"] += added.get(day, 0)
        running["removed"] += removed.get(day, 0)
        running["completed"] += completed.get(day, 0)
        remaining = None
        if day <= last_actual_day:
            remaining = (
                sprint.committed_points
                + running["added"]
                - running["removed"]
                - running["completed"]
            )
        days.append({"date": day, "ideal": ideal_remaining(sprint, day), "remaining": remaining})
    return {
        "sprint_id": sprint.pk,
        "start_date": sprint.start_date,
        "end_date": sprint.end_date,
        "committed_points": sprint.committed_points,
        "days": days,
    }


def ideal_remaining(sprint: Sprint, day: date) -> float:
    """Straight line from committed_points on start_date to 0 on end_date (2 decimals)."""
    total_days = (sprint.end_date - sprint.start_date).days
    if total_days == 0:
        return 0.0
    days_left = (sprint.end_date - day).days
    return round(sprint.committed_points * days_left / total_days, 2)


def velocity(project_id: int) -> dict[str, Any]:
    """Completed points per completed sprint and the average of the last 3.

    Example: last three sprints completed 8, 5 and 6 points -> average 6.33.
    With no completed sprint the average is 0 (no division by zero).
    """
    completed = Sprint.objects.filter(
        project_id=project_id, status=Sprint.Status.COMPLETED
    ).order_by("-completed_at", "-end_date", "-id")
    history = list(
        completed.values("id", "name", "committed_points", "completed_points", "end_date")[
            :VELOCITY_HISTORY
        ]
    )
    window = [row["completed_points"] for row in history[:VELOCITY_WINDOW]]
    average = round(sum(window) / len(window), 2) if window else 0
    return {"sprints": list(reversed(history)), "average_velocity": average}


def _burndown_events(sprint: Sprint) -> tuple[dict[date, int], dict[date, int], dict[date, int]]:
    """Points per local day: added, removed (scope changes) and completed tasks (3 queries)."""
    changes = SprintScopeChange.objects.filter(sprint=sprint)
    added = _points_per_day(changes.filter(kind=SprintScopeChange.Kind.ADDED), "at", "points")
    removed = _points_per_day(changes.filter(kind=SprintScopeChange.Kind.REMOVED), "at", "points")
    done_tasks = Task.objects.filter(sprint=sprint, status=Task.Status.DONE)
    completed = _points_per_day(done_tasks, "completed_at", "story_points")
    return added, removed, completed


def _points_per_day(queryset: QuerySet, datetime_field: str, points_field: str) -> dict[date, int]:
    """GROUP BY the local date of `datetime_field` and SUM the points (in PostgreSQL)."""
    rows = (
        queryset.annotate(day=TruncDate(datetime_field, tzinfo=timezone.get_default_timezone()))
        .values("day")
        .annotate(points=Coalesce(Sum(points_field), 0))
        .order_by()
    )
    return {row["day"]: row["points"] for row in rows}


def _sum_before(points_by_day: dict[date, int], first_day: date) -> int:
    return sum(points for day, points in points_by_day.items() if day < first_day)


def _date_range(first: date, last: date) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
