"""
Write operations for sprints: create, plan (add/remove tasks), start, complete.

Called by `sprints/api_views.py` (and template views). Every function locks the
project row via `lock_project_and_recheck`, so "only one active sprint" and
"a task is in at most one sprint" cannot be broken by two requests at once.
Burndown/velocity reads live in `sprints/selectors.py`, never here.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.db.models.functions import Coalesce
from django.http import Http404

from activity.services import log_activity
from core import clock
from core.exceptions import BusinessRuleError
from projects.models import ProjectMember
from projects.permissions import Action
from projects.services import lock_project_and_recheck
from sprints.models import Sprint, SprintScopeChange
from tasks.models import Task

Status = Sprint.Status


class UnfinishedTaskAction:
    """What happens to not-done tasks when a sprint is completed."""

    BACKLOG = "backlog"
    SPRINT = "sprint"
    CHOICES = (BACKLOG, SPRINT)


def create_sprint(
    *, membership: ProjectMember, name: str, start_date: date, end_date: date, goal: str = ""
) -> Sprint:
    """Create a planned sprint (managers and owners).

    Raises:
        ValidationError: empty name or start_date after end_date.
    """
    _validate_sprint_fields(name, start_date, end_date)
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_SPRINTS)
        sprint = Sprint.objects.create(
            project=actor.project,
            name=name.strip(),
            goal=goal,
            start_date=start_date,
            end_date=end_date,
        )
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="sprint.created",
            target=sprint,
            changes={"name": sprint.name, "start": start_date, "end": end_date},
        )
    return sprint


def update_sprint(
    *,
    membership: ProjectMember,
    sprint_id: int,
    name: str | None = None,
    goal: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Sprint:
    """Edit a planned or active sprint. Completed sprints are history and cannot change."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_SPRINTS)
        sprint = _get_sprint(actor.project_id, sprint_id)
        if sprint.status == Status.COMPLETED:
            raise BusinessRuleError("A completed sprint cannot be edited.", code="sprint_completed")
        new_name = sprint.name if name is None else name
        new_start = start_date or sprint.start_date
        new_end = end_date or sprint.end_date
        _validate_sprint_fields(new_name, new_start, new_end)
        sprint.name = new_name.strip()
        sprint.goal = sprint.goal if goal is None else goal
        sprint.start_date, sprint.end_date = new_start, new_end
        sprint.save()
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="sprint.updated",
            target=sprint,
            changes={"name": sprint.name, "start": new_start, "end": new_end},
        )
    return sprint


def delete_sprint(*, membership: ProjectMember, sprint_id: int) -> None:
    """Delete a planned sprint; its tasks go back to the backlog."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_SPRINTS)
        sprint = _get_sprint(actor.project_id, sprint_id)
        if sprint.status != Status.PLANNED:
            raise BusinessRuleError(
                "Only planned sprints can be deleted.", code="sprint_not_planned"
            )
        moved = Task.objects.filter(sprint=sprint).update(sprint=None, version=F("version") + 1)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="sprint.deleted",
            target=sprint,
            changes={"name": sprint.name, "tasks_to_backlog": moved},
        )
        sprint.delete()


def add_task_to_sprint(*, membership: ProjectMember, sprint_id: int, task_number: int) -> Task:
    """Put a backlog task into a planned or active sprint.

    Why: a task belongs to at most one sprint, and adding to an ACTIVE sprint
    changes its scope, which the burndown must know about.

    Raises:
        BusinessRuleError: sprint completed, task already in a sprint, task done.
    """
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_SPRINTS)
        sprint = _get_sprint(actor.project_id, sprint_id)
        task = _get_task(actor.project_id, task_number)
        if sprint.status == Status.COMPLETED:
            raise BusinessRuleError(
                "Tasks cannot be added to a completed sprint.", code="sprint_completed"
            )
        # RULE: a task is in at most one sprint at a time.
        if task.sprint_id is not None:
            raise BusinessRuleError(
                "This task is already in a sprint. Remove it from that sprint first.",
                code="task_in_other_sprint",
            )
        if task.status == Task.Status.DONE:
            raise BusinessRuleError("Done tasks cannot be planned into a sprint.", code="task_done")
        _set_task_sprint(task, sprint)
        if sprint.status == Status.ACTIVE:
            record_scope_change(sprint, task, SprintScopeChange.Kind.ADDED)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="sprint.task_added",
            target=sprint,
            changes={"task": task.number},
        )
    return task


def remove_task_from_sprint(*, membership: ProjectMember, sprint_id: int, task_number: int) -> Task:
    """Send a task from a planned/active sprint back to the backlog."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_SPRINTS)
        sprint = _get_sprint(actor.project_id, sprint_id)
        task = _get_task(actor.project_id, task_number)
        if task.sprint_id != sprint.pk:
            raise Http404("Task is not in this sprint.")
        if sprint.status == Status.COMPLETED:
            raise BusinessRuleError("Completed sprints cannot change.", code="sprint_completed")
        _set_task_sprint(task, None)
        if sprint.status == Status.ACTIVE:
            record_scope_change(sprint, task, SprintScopeChange.Kind.REMOVED)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="sprint.task_removed",
            target=sprint,
            changes={"task": task.number},
        )
    return task


def start_sprint(*, membership: ProjectMember, sprint_id: int) -> Sprint:
    """Make a planned sprint active and snapshot its committed points.

    Why: only one sprint per project may be active, otherwise "the current
    sprint" and its burndown are ambiguous.

    How: the project row is locked and we check for another active sprint.
    The partial unique index `one_active_sprint_per_project` is a second guard.

    Example:
        tasks with points 3, 5 and null -> committed_points = 8.

    Raises:
        BusinessRuleError: sprint not planned, or another sprint is already active.
    """
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_SPRINTS)
        sprint = _get_sprint(actor.project_id, sprint_id)
        if sprint.status != Status.PLANNED:
            raise BusinessRuleError(
                "Only a planned sprint can be started.", code="sprint_not_planned"
            )
        # RULE: one active sprint per project (checked under the project lock).
        if Sprint.objects.filter(project_id=actor.project_id, status=Status.ACTIVE).exists():
            raise BusinessRuleError(
                "Another sprint is already active. Complete it first.", code="sprint_already_active"
            )
        sprint.status = Status.ACTIVE
        sprint.committed_points = _points_of(Task.objects.filter(sprint=sprint))
        sprint.started_at = clock.now()
        try:
            # RACE: the partial unique index is the last line of defence.
            with transaction.atomic():
                sprint.save(update_fields=["status", "committed_points", "started_at"])
        except IntegrityError as exc:
            raise BusinessRuleError(
                "Another sprint is already active. Complete it first.", code="sprint_already_active"
            ) from exc
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="sprint.started",
            target=sprint,
            changes={"committed_points": sprint.committed_points},
        )
    return sprint


def complete_sprint(
    *,
    membership: ProjectMember,
    sprint_id: int,
    unfinished_action: str,
    target_sprint_id: int | None = None,
) -> Sprint:
    """Close the active sprint and move its unfinished tasks in one transaction.

    Why: unfinished work must never be silently lost; the caller chooses
    "backlog" (sprint = None) or "sprint" (a planned sprint of the same project).
    Done tasks stay linked to the completed sprint as history.

    Example:
        tasks: A done 5 pts, B done 3 pts, C in_progress 2 pts
        -> completed_points = 8, C moves to the backlog or the chosen sprint.

    Raises:
        BusinessRuleError: sprint not active, invalid target sprint.
        ValidationError: unknown unfinished_action.
    """
    if unfinished_action not in UnfinishedTaskAction.CHOICES:
        raise ValidationError({"unfinished_action": ["Choose 'backlog' or 'sprint'."]})
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_SPRINTS)
        sprint = _get_sprint(actor.project_id, sprint_id)
        if sprint.status != Status.ACTIVE:
            raise BusinessRuleError(
                "Only the active sprint can be completed.", code="sprint_not_active"
            )
        target = _resolve_target_sprint(
            actor.project_id, sprint, unfinished_action, target_sprint_id
        )
        sprint_tasks = Task.objects.filter(sprint=sprint)
        sprint.completed_points = _points_of(sprint_tasks.filter(status=Task.Status.DONE))
        moved = sprint_tasks.exclude(status=Task.Status.DONE).update(
            sprint=target, version=F("version") + 1
        )
        sprint.status = Status.COMPLETED
        sprint.completed_at = clock.now()
        sprint.save(update_fields=["status", "completed_points", "completed_at"])
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="sprint.completed",
            target=sprint,
            changes={
                "completed_points": sprint.completed_points,
                "unfinished_moved": moved,
                "unfinished_to": target.name if target else "backlog",
            },
        )
    return sprint


def record_scope_change(
    sprint: Sprint, task: Task, kind: str, points: int | None = None
) -> SprintScopeChange:
    """Remember a mid-sprint scope change for the burndown (call inside a transaction).

    `points` defaults to the task's story points (null = 0).
    """
    return SprintScopeChange.objects.create(
        sprint=sprint,
        task=task,
        task_number=task.number,
        kind=kind,
        points=(task.story_points or 0) if points is None else points,
        at=clock.now(),
    )


def record_story_point_change(task: Task, old_points: int | None) -> None:
    """RULE: re-estimating a task of the active sprint is a scope change of the difference.

    Example: 3 -> 5 points records "added 2"; 5 -> null records "removed 5".
    """
    if task.sprint is None or task.sprint.status != Status.ACTIVE:
        return
    delta = (task.story_points or 0) - (old_points or 0)
    if delta > 0:
        record_scope_change(task.sprint, task, SprintScopeChange.Kind.ADDED, delta)
    elif delta < 0:
        record_scope_change(task.sprint, task, SprintScopeChange.Kind.REMOVED, -delta)


def _resolve_target_sprint(
    project_id: int, sprint: Sprint, action: str, target_sprint_id: int | None
) -> Sprint | None:
    if action == UnfinishedTaskAction.BACKLOG:
        return None
    if target_sprint_id is None:
        raise ValidationError({"target_sprint_id": ["Choose the sprint for unfinished tasks."]})
    target = _get_sprint(project_id, target_sprint_id)
    if target.pk == sprint.pk or target.status != Status.PLANNED:
        raise BusinessRuleError(
            "Unfinished tasks can only move to a planned sprint.", code="invalid_target_sprint"
        )
    return target


def _set_task_sprint(task: Task, sprint: Sprint | None) -> None:
    """Change task.sprint and bump its version so open editors reload."""
    Task.objects.filter(pk=task.pk).update(sprint=sprint, version=F("version") + 1)
    task.sprint = sprint


def _points_of(tasks) -> int:
    """Sum of story points; tasks without points count as 0."""
    return tasks.aggregate(total=Coalesce(Sum("story_points"), 0))["total"]


def _validate_sprint_fields(name: str, start_date: date, end_date: date) -> None:
    errors = {}
    if not (name or "").strip():
        errors["name"] = ["Name is required."]
    # RULE: start_date <= end_date (also a DB CheckConstraint).
    if start_date > end_date:
        errors["end_date"] = ["End date must be on or after the start date."]
    if errors:
        raise ValidationError(errors)


def _get_sprint(project_id: int, sprint_id: int) -> Sprint:
    # SAFETY: scoped to the actor's project.
    sprint = Sprint.objects.filter(project_id=project_id, pk=sprint_id).first()
    if sprint is None:
        raise Http404("Sprint not found.")
    return sprint


def _get_task(project_id: int, number: int) -> Task:
    task = Task.objects.filter(project_id=project_id, number=number).first()
    if task is None:
        raise Http404("Task not found.")
    return task
