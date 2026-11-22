"""
Read-only project overview for the dashboard (API and template page).

Combines selectors from tasks and sprints. Aggregation happens in PostgreSQL;
the number of queries is fixed no matter how many tasks exist.
Callers must have proven membership first.
"""

from typing import Any

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce

from core import clock
from sprints.models import Sprint
from sprints.selectors import active_sprint, velocity
from tasks.models import Task
from tasks.report_selectors import workload


def project_dashboard(project_id: int) -> dict[str, Any]:
    """Tasks per status, overdue count, current sprint progress, velocity and workload."""
    counts = task_counts(project_id)
    overdue = counts.pop("overdue")
    return {
        "tasks_by_status": counts,
        "overdue": overdue,
        "current_sprint": current_sprint_progress(project_id),
        "average_velocity": velocity(project_id)["average_velocity"],
        "workload": workload(project_id),
    }


def task_counts(project_id: int) -> dict[str, int]:
    """{status: count} for every status (zeros included) plus "overdue", in ONE query.

    RULE: overdue = due before today (project timezone) and not done.
    How: COUNT(*) FILTER (WHERE ...) per bucket, so PostgreSQL scans the tasks once.
    """
    counts = {status: Count("id", filter=Q(status=status)) for status in Task.Status.values}
    counts["overdue"] = Count(
        "id", filter=Q(due_date__lt=clock.today()) & ~Q(status=Task.Status.DONE)
    )
    return Task.objects.filter(project_id=project_id).aggregate(**counts)


def current_sprint_progress(project_id: int) -> dict[str, Any] | None:
    """Progress of the active sprint, or None when no sprint is running.

    Example: committed 20, done tasks worth 5 -> done_points 5, progress_percent 25.0.
    """
    sprint: Sprint | None = active_sprint(project_id)
    if sprint is None:
        return None
    totals = Task.objects.filter(sprint=sprint).aggregate(
        done_points=Coalesce(Sum("story_points", filter=Q(status=Task.Status.DONE)), 0),
        done_tasks=Count("id", filter=Q(status=Task.Status.DONE)),
        total_tasks=Count("id"),
    )
    committed = sprint.committed_points
    progress = round(totals["done_points"] * 100 / committed, 1) if committed else 0.0
    return {
        "id": sprint.pk,
        "name": sprint.name,
        "start_date": sprint.start_date,
        "end_date": sprint.end_date,
        "committed_points": committed,
        "done_points": totals["done_points"],
        "done_tasks": totals["done_tasks"],
        "total_tasks": totals["total_tasks"],
        "progress_percent": progress,
        "days_left": max((sprint.end_date - clock.today()).days, 0),
    }
