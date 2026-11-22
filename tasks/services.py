"""
Write operations for tasks and labels.

Called by `tasks/api_views.py` (and later template views). Every write:
- locks the project row via `projects.services.lock_project_and_recheck`
  (serialises task numbering, board positions and the dependency graph),
- uses optimistic locking (`version`) for task updates, so a stale edit gets
  409 instead of silently overwriting someone else's change,
- writes its ActivityLog row in the same transaction.
Workflow rules live in `tasks.workflow`; never re-implement them here.
"""

from datetime import date
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Max
from django.http import Http404

from activity.models import Notification
from activity.services import log_activity, notify
from core import clock
from core.exceptions import VersionConflict
from projects.models import Project, ProjectMember
from projects.permissions import Action, can_delete_task, is_assignable, require_permission
from projects.services import lock_project_and_recheck
from sprints.models import Sprint, SprintScopeChange
from sprints.services import record_scope_change, record_story_point_change
from tasks import workflow
from tasks.models import Label, Task

INITIAL_STATUSES = {Task.Status.BACKLOG, Task.Status.TODO}
EDITABLE_FIELDS = {
    "title",
    "description",
    "priority",
    "assignee_id",
    "due_date",
    "story_points",
    "estimate_minutes",
    "status",
}


def task_snapshot(task: Task) -> dict[str, Any]:
    """Plain-dict view of a task, returned inside a 409 so the client can merge."""
    return {
        "number": task.number,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "assignee_id": task.assignee_id,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "story_points": task.story_points,
        "estimate_minutes": task.estimate_minutes,
        "position": task.position,
        "version": task.version,
        "label_ids": sorted(task.labels.values_list("id", flat=True)),
    }


def create_task(
    *,
    membership: ProjectMember,
    title: str,
    description: str = "",
    status: str = Task.Status.BACKLOG,
    priority: str = Task.Priority.MEDIUM,
    assignee_id: int | None = None,
    due_date: date | None = None,
    story_points: int | None = None,
    estimate_minutes: int | None = None,
    label_ids: list[int] | None = None,
) -> Task:
    """Create a task with the next sequential number of its project.

    Why: numbers must be 1, 2, 3... per project with no duplicates even when
    20 people create tasks at the same moment.

    How: the project row is locked, so `task_counter` is read and incremented
    by one transaction at a time. If anything fails later the transaction rolls
    back and the counter increment is undone too (no number is wasted).

    Example:
        counter=11 -> new task gets number 12, counter becomes 12 -> key "WEB-12".

    Raises:
        ValidationError: bad status/assignee/labels.
        PermissionDenied / Http404: via the permission module.
    """
    if status not in INITIAL_STATUSES:
        raise ValidationError({"status": ["New tasks start in backlog or todo."]})
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.CREATE_TASK)
        project = actor.project
        _validate_assignee(project.pk, assignee_id)
        labels = _resolve_labels(project.pk, label_ids)
        # RACE: safe because the project row is locked by lock_project_and_recheck.
        project.task_counter += 1
        project.save(update_fields=["task_counter", "updated_at"])
        task = Task.objects.create(
            project=project,
            number=project.task_counter,
            title=_clean_title(title),
            description=description,
            status=status,
            priority=priority,
            reporter=actor.user,
            assignee_id=assignee_id,
            due_date=due_date,
            story_points=story_points,
            estimate_minutes=estimate_minutes,
            position=_next_position(project.pk, status),
        )
        task.labels.set(labels)
        log_activity(
            project=project,
            actor=actor.user,
            verb="task.created",
            target=task,
            changes={"number": task.number, "title": task.title, "status": status},
        )
        _notify_assignment(task, actor, previous_assignee_id=None)
    return task


def update_task(
    *, membership: ProjectMember, number: int, expected_version: int, changes: dict[str, Any]
) -> Task:
    """Apply a partial update only if nobody changed the task since `expected_version`.

    Why: two people editing the same task must never silently overwrite each other.

    How: `UPDATE task SET ..., version = version + 1 WHERE id = X AND version = V`.
    0 rows updated means someone else saved first -> VersionConflict (409).

    Example:
        Asha and Ravi both load WEB-5 at version 3. Asha saves -> version 4.
        Ravi saves with version 3 -> 409 with the current task (version 4).

    Raises:
        VersionConflict: stale `expected_version`.
        BusinessRuleError: workflow rule broken (see tasks.workflow).
        ValidationError: invalid field values.
    """
    label_ids = changes.pop("label_ids", None)
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValidationError({"fields": [f"Not editable: {', '.join(sorted(unknown))}."]})
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.EDIT_TASK)
        task = _get_task_for_update(actor.project_id, number)
        _check_version(task, expected_version)
        old_points = task.story_points
        fields = _build_update_fields(actor, task, changes)
        labels = _resolve_labels(actor.project_id, label_ids) if label_ids is not None else None
        _apply_versioned_update(task, expected_version, fields)
        if labels is not None:
            task.labels.set(labels)
        task.refresh_from_db()
        if "story_points" in fields:
            record_story_point_change(task, old_points)
        changed = {
            name: value
            for name, value in fields.items()
            if name != "position" and not name.startswith("_")
        }
        if labels is not None:
            changed["label_ids"] = sorted(label.pk for label in labels)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="task.updated",
            target=task,
            changes=changed,
        )
        if "assignee_id" in fields:
            _notify_assignment(task, actor, previous_assignee_id=fields["_old_assignee_id"])
    return task


def move_task(
    *,
    membership: ProjectMember,
    number: int,
    expected_version: int,
    to_status: str,
    to_position: int,
) -> Task:
    """Move a card on the board: change column and/or position atomically.

    Why: the board must never show two cards fighting for one slot, and a
    status change through drag-and-drop follows the same workflow rules.

    How: inside the project lock, read the target column's card ids in order,
    insert this card at `to_position` (clamped to the column size) and write
    positions 0..n-1. Other cards only get a new `position`; their `version`
    is not bumped, because reordering is not an edit of their content.

    Example:
        todo column = [A(0), B(1), C(2)]; move D into todo at position 1
        -> [A(0), D(1), B(2), C(3)].
    """
    if to_status not in Task.Status.values:
        raise ValidationError({"status": ["Unknown status."]})
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.CHANGE_TASK_STATUS)
        task = _get_task_for_update(actor.project_id, number)
        _check_version(task, expected_version)
        old_status = task.status
        workflow.validate_transition(task, to_status, task.assignee_id)
        column_ids = list(
            Task.objects.filter(project_id=actor.project_id, status=to_status)
            .exclude(pk=task.pk)
            .order_by("position", "number")
            .values_list("pk", flat=True)
        )
        insert_at = max(0, min(to_position, len(column_ids)))
        column_ids.insert(insert_at, task.pk)
        _write_positions(column_ids, moved_task_id=task.pk)
        fields = {
            "status": to_status,
            "position": insert_at,
            "completed_at": workflow.completed_at_after(to_status, old_status, task.completed_at),
        }
        _apply_versioned_update(task, expected_version, fields)
        task.refresh_from_db()
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="task.moved",
            target=task,
            changes={"status": [old_status, to_status], "position": insert_at},
        )
    return task


def delete_task(*, membership: ProjectMember, number: int) -> None:
    """Delete a task. Its dependency edges are removed with it (DB cascade).

    Raises:
        PermissionDenied: members may delete only tasks they reported.
    """
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.DELETE_OWN_TASK)
        task = _get_task_for_update(actor.project_id, number)
        # SAFETY: own-task rule for members; managers/owners delete any.
        if not can_delete_task(actor, task):
            raise PermissionDenied("You can delete only tasks you created.")
        # RULE: deleting a task of the active sprint is a scope change for the burndown.
        if task.sprint is not None and task.sprint.status == Sprint.Status.ACTIVE:
            record_scope_change(task.sprint, task, SprintScopeChange.Kind.REMOVED)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="task.deleted",
            target=task,
            changes={"number": task.number, "title": task.title},
        )
        task.delete()


def create_label(*, membership: ProjectMember, name: str, color: str) -> Label:
    """Create a label; names are unique per project ignoring case."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_LABELS)
        name = _clean_label_name(actor.project_id, name)
        label = Label.objects.create(project=actor.project, name=name, color=color)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="label.created",
            target=label,
            changes={"name": name, "color": color},
        )
    return label


def update_label(
    *, membership: ProjectMember, label_id: int, name: str | None = None, color: str | None = None
) -> Label:
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_LABELS)
        label = _get_label(actor.project_id, label_id)
        if name is not None:
            label.name = _clean_label_name(actor.project_id, name, exclude_id=label.pk)
        if color is not None:
            label.color = color
        label.save()
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="label.updated",
            target=label,
            changes={"name": label.name, "color": label.color},
        )
    return label


def delete_label(*, membership: ProjectMember, label_id: int) -> None:
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_LABELS)
        label = _get_label(actor.project_id, label_id)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="label.deleted",
            target=label,
            changes={"name": label.name},
        )
        label.delete()


# ---------------------------------------------------------------- helpers


def _get_task_for_update(project_id: int, number: int) -> Task:
    # SAFETY: scoped by project, so another project's task number is a 404.
    task = Task.objects.select_related("project", "sprint").filter(
        project_id=project_id, number=number
    )
    task = task.first()
    if task is None:
        raise Http404("Task not found.")
    return task


def _check_version(task: Task, expected_version: int) -> None:
    if task.version != expected_version:
        raise VersionConflict(task_snapshot(task))


def _apply_versioned_update(task: Task, expected_version: int, fields: dict[str, Any]) -> None:
    """RACE: compare-and-set on `version`; 0 rows means a concurrent save won."""
    db_fields = {name: value for name, value in fields.items() if not name.startswith("_")}
    updated_rows = Task.objects.filter(pk=task.pk, version=expected_version).update(
        **db_fields, version=F("version") + 1, updated_at=clock.now()
    )
    if updated_rows == 0:
        task.refresh_from_db()
        raise VersionConflict(task_snapshot(task))


def _build_update_fields(actor: ProjectMember, task: Task, changes: dict[str, Any]) -> dict:
    """Validate requested changes and return the column values to write."""
    fields: dict[str, Any] = {}
    if "title" in changes:
        fields["title"] = _clean_title(changes["title"])
    for name in ("description", "priority", "due_date", "story_points", "estimate_minutes"):
        if name in changes:
            fields[name] = changes[name]
    if "assignee_id" in changes and changes["assignee_id"] != task.assignee_id:
        _validate_assignee(task.project_id, changes["assignee_id"])
        fields["assignee_id"] = changes["assignee_id"]
        fields["_old_assignee_id"] = task.assignee_id
    new_status = changes.get("status", task.status)
    if new_status != task.status:
        require_permission(actor, Action.CHANGE_TASK_STATUS)
        assignee_after = fields.get("assignee_id", task.assignee_id)
        workflow.validate_transition(task, new_status, assignee_after)
        fields["status"] = new_status
        fields["completed_at"] = workflow.completed_at_after(
            new_status, task.status, task.completed_at
        )
        # RULE: a card that changes column goes to the bottom of the new column.
        fields["position"] = _next_position(task.project_id, new_status)
    return fields


def _write_positions(ordered_ids: list[int], moved_task_id: int) -> None:
    """Store positions 0..n-1 for the given column order (except the moved card)."""
    others = Task.objects.filter(pk__in=ordered_ids).exclude(pk=moved_task_id).only("position")
    by_id = {task.pk: task for task in others}
    to_update = []
    for position, task_id in enumerate(ordered_ids):
        task = by_id.get(task_id)
        if task is not None and task.position != position:
            task.position = position
            to_update.append(task)
    Task.objects.bulk_update(to_update, ["position"])


def _next_position(project_id: int, status: str) -> int:
    highest = Task.objects.filter(project_id=project_id, status=status).aggregate(
        highest=Max("position")
    )["highest"]
    return 0 if highest is None else highest + 1


def _validate_assignee(project_id: int, assignee_id: int | None) -> None:
    """RULE: the assignee must be a project member who is not a viewer."""
    if assignee_id is None:
        return
    membership = ProjectMember.objects.filter(project_id=project_id, user_id=assignee_id).first()
    if not is_assignable(membership):
        raise ValidationError(
            {"assignee_id": ["Assignee must be a project member who is not a viewer."]}
        )


def _resolve_labels(project_id: int, label_ids: list[int] | None) -> list[Label]:
    if not label_ids:
        return []
    # SAFETY: only labels of this project; foreign ids are rejected, not ignored.
    labels = list(Label.objects.filter(project_id=project_id, pk__in=set(label_ids)))
    if len(labels) != len(set(label_ids)):
        raise ValidationError({"label_ids": ["One or more labels do not exist in this project."]})
    return labels


def _get_label(project_id: int, label_id: int) -> Label:
    label = Label.objects.filter(project_id=project_id, pk=label_id).first()
    if label is None:
        raise Http404("Label not found.")
    return label


def _clean_label_name(project_id: int, name: str, exclude_id: int | None = None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": ["Name is required."]})
    duplicates = Label.objects.filter(project_id=project_id, name__iexact=name)
    if exclude_id is not None:
        duplicates = duplicates.exclude(pk=exclude_id)
    if duplicates.exists():
        raise ValidationError({"name": ["A label with this name already exists."]})
    return name


def _clean_title(title: str) -> str:
    title = (title or "").strip()
    if not title:
        raise ValidationError({"title": ["Title is required."]})
    return title


def _notify_assignment(task: Task, actor: ProjectMember, previous_assignee_id: int | None) -> None:
    """RULE: notify the new assignee, except when people assign themselves."""
    if task.assignee_id is None or task.assignee_id == previous_assignee_id:
        return
    if task.assignee_id == actor.user_id:
        return
    project: Project = actor.project
    notify(
        user=task.assignee,
        project=project,
        type=Notification.Type.ASSIGNED,
        message=f"{actor.user.username} assigned you {project.key}-{task.number}: {task.title}",
        link=f"/projects/{project.pk}/tasks/{task.number}/",
        task=task,
    )
