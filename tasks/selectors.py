"""
Read queries for tasks, labels, comments, time entries and dependencies.

Callers must have already proven project membership with
`projects.permissions.get_membership_or_404`; every query here is scoped by
`project_id` (or, for `my_tasks`, by the user's memberships), so a task from
another project can never be returned (no IDOR).
"""

import re
from datetime import date
from typing import Any

from django.db.models import Case, F, IntegerField, Prefetch, Q, QuerySet, Value, When
from django.http import Http404

from core import clock
from projects.permissions import visible_projects_filter
from tasks.models import Comment, Label, Task, TaskDependency, TimeEntry

PRIORITY_RANK = {
    Task.Priority.LOW: 1,
    Task.Priority.MEDIUM: 2,
    Task.Priority.HIGH: 3,
    Task.Priority.URGENT: 4,
}

# Public ordering name -> ORM ordering. Anything else is rejected by the API.
TASK_ORDERINGS = {
    "number": ("number",),
    "-number": ("-number",),
    "priority": ("priority_rank", "number"),
    "-priority": ("-priority_rank", "number"),
    "due_date": ("due_date", "number"),
    "-due_date": ("-due_date", "number"),
    "created_at": ("created_at", "number"),
    "-created_at": ("-created_at", "-number"),
    "updated_at": ("updated_at", "number"),
    "-updated_at": ("-updated_at", "-number"),
    "position": ("status", "position", "number"),
}

TASK_KEY_SEARCH = re.compile(r"^(?:[A-Za-z]{2,6}-)?(\d+)$")


def _priority_rank() -> Case:
    """SQL CASE turning priority text into 1..4 so ordering follows business importance."""
    return Case(
        *[When(priority=key, then=Value(rank)) for key, rank in PRIORITY_RANK.items()],
        output_field=IntegerField(),
    )


def _with_related(queryset: QuerySet[Task]) -> QuerySet[Task]:
    """Load everything a task response needs in a fixed number of queries (no N+1)."""
    return queryset.select_related("project", "reporter", "assignee", "sprint").prefetch_related(
        "labels",
        Prefetch(
            "blocked_by_links",
            queryset=TaskDependency.objects.select_related("blocker").order_by("blocker__number"),
        ),
        Prefetch(
            "blocking_links",
            queryset=TaskDependency.objects.select_related("blocked").order_by("blocked__number"),
        ),
    )


def get_task_or_404(project_id: int, number: int) -> Task:
    """One task of the project by its number, with related data loaded."""
    task = _with_related(Task.objects.filter(project_id=project_id, number=number)).first()
    if task is None:
        raise Http404("Task not found.")
    return task


def list_tasks(project_id: int, filters: dict[str, Any], user=None) -> QuerySet[Task]:
    """Filtered, searched and ordered tasks of one project.

    Args:
        filters: already-validated values. Supported keys: status (list),
            priority (list), assignee ("me", "none" or a user id), sprint
            ("none" = backlog, or a sprint id), label (id), due_before,
            due_after (dates), overdue (bool), q (text), ordering.
        user: needed only for assignee="me".
    """
    queryset = Task.objects.filter(project_id=project_id).annotate(priority_rank=_priority_rank())
    queryset = _apply_filters(queryset, filters, user)
    ordering = TASK_ORDERINGS[filters.get("ordering") or "number"]
    return _with_related(queryset.order_by(*ordering))


def _apply_filters(queryset: QuerySet[Task], filters: dict[str, Any], user) -> QuerySet[Task]:
    if filters.get("status"):
        queryset = queryset.filter(status__in=filters["status"])
    if filters.get("priority"):
        queryset = queryset.filter(priority__in=filters["priority"])
    queryset = _filter_assignee(queryset, filters.get("assignee"), user)
    queryset = _filter_sprint(queryset, filters.get("sprint"))
    if filters.get("label"):
        queryset = queryset.filter(labels__id=filters["label"])
    if filters.get("due_before"):
        queryset = queryset.filter(due_date__lte=filters["due_before"])
    if filters.get("due_after"):
        queryset = queryset.filter(due_date__gte=filters["due_after"])
    if filters.get("overdue"):
        queryset = overdue_filter(queryset, clock.today())
    if filters.get("q"):
        queryset = queryset.filter(_search_condition(filters["q"]))
    return queryset


def _filter_assignee(queryset: QuerySet[Task], assignee: str | None, user) -> QuerySet[Task]:
    if assignee == "me":
        return queryset.filter(assignee=user)
    if assignee == "none":
        return queryset.filter(assignee__isnull=True)
    if assignee:
        return queryset.filter(assignee_id=int(assignee))
    return queryset


def _filter_sprint(queryset: QuerySet[Task], sprint: str | None) -> QuerySet[Task]:
    if sprint == "none":
        return queryset.filter(sprint__isnull=True)
    if sprint:
        return queryset.filter(sprint_id=int(sprint))
    return queryset


def backlog(project_id: int) -> QuerySet[Task]:
    """RULE: the backlog = open tasks not planned into any sprint, most important first."""
    open_statuses = [status for status in Task.Status.values if status != Task.Status.DONE]
    filters = {"sprint": "none", "status": open_statuses, "ordering": "-priority"}
    return list_tasks(project_id, filters)


def overdue_filter(queryset: QuerySet[Task], today: date) -> QuerySet[Task]:
    """RULE: overdue = has a due date before today and is not done."""
    return queryset.filter(due_date__lt=today).exclude(status=Task.Status.DONE)


def _search_condition(text: str) -> Q:
    """Match title/description text, or a task key like "WEB-12" / "12"."""
    text = text.strip()
    condition = Q(title__icontains=text) | Q(description__icontains=text)
    key_match = TASK_KEY_SEARCH.match(text)
    if key_match:
        condition |= Q(number=int(key_match.group(1)))
    return condition


def open_blockers(task_id: int) -> list[Task]:
    """Tasks that block `task_id` and are not done yet, ordered by number."""
    return list(
        Task.objects.filter(blocking_links__blocked_id=task_id)
        .exclude(status=Task.Status.DONE)
        .select_related("project")
        .order_by("number")
    )


def dependency_graph(project_id: int) -> dict[int, set[int]]:
    """Adjacency map {blocker_id: {blocked_id, ...}} for the whole project (one query)."""
    graph: dict[int, set[int]] = {}
    edges = TaskDependency.objects.filter(project_id=project_id).values_list(
        "blocker_id", "blocked_id"
    )
    for blocker_id, blocked_id in edges:
        graph.setdefault(blocker_id, set()).add(blocked_id)
    return graph


def board(project_id: int) -> dict[str, list[Task]]:
    """Tasks grouped by status column, each column ordered by position."""
    columns: dict[str, list[Task]] = {status: [] for status in Task.Status.values}
    tasks = _with_related(Task.objects.filter(project_id=project_id).order_by("position", "number"))
    for task in tasks:
        columns[task.status].append(task)
    return columns


def my_tasks(user, include_done: bool = False) -> QuerySet[Task]:
    """Tasks assigned to `user` across all of the user's projects.

    SAFETY: limited to projects where the user is still a member.
    Ordered by due date (no due date last), then priority, then project/number.
    """
    queryset = Task.objects.filter(
        visible_projects_filter(user, project_field="project_id"), assignee=user
    )
    if not include_done:
        queryset = queryset.exclude(status=Task.Status.DONE)
    queryset = queryset.annotate(priority_rank=_priority_rank())
    ordering = (F("due_date").asc(nulls_last=True), "-priority_rank", "project__key", "number")
    return _with_related(queryset.order_by(*ordering))


def task_comments(task_id: int) -> QuerySet[Comment]:
    """Comments of one task, oldest first, with authors loaded."""
    return (
        Comment.objects.filter(task_id=task_id)
        .select_related("author")
        .order_by("created_at", "id")
    )


def task_time_entries(task_id: int) -> QuerySet[TimeEntry]:
    """Time entries of one task, newest first, with users loaded."""
    return TimeEntry.objects.filter(task_id=task_id).select_related("user").order_by("-date", "-id")


def project_labels(project_id: int) -> QuerySet[Label]:
    return Label.objects.filter(project_id=project_id).order_by("name", "id")
