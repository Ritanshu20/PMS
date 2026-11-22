"""
Task status workflow: the ONE place that defines which status moves are allowed.

Used by `tasks.services.update_task` and `tasks.services.move_task`.
Pure rules only: this module never writes to the database.

Flow: backlog -> todo -> in_progress -> in_review -> done
- Forward: exactly one step at a time.
- Backward (reopen): to any earlier status.
"""

from datetime import datetime

from core import clock
from core.exceptions import BusinessRuleError
from tasks.models import Task
from tasks.selectors import open_blockers

Status = Task.Status

STATUS_ORDER: list[str] = [
    Status.BACKLOG,
    Status.TODO,
    Status.IN_PROGRESS,
    Status.IN_REVIEW,
    Status.DONE,
]


def allowed_next_statuses(current: str) -> set[str]:
    """Statuses a task in `current` may move to.

    Example:
        allowed_next_statuses("in_progress") == {"in_review", "backlog", "todo"}
    """
    index = STATUS_ORDER.index(current)
    earlier = set(STATUS_ORDER[:index])
    next_step = {STATUS_ORDER[index + 1]} if index + 1 < len(STATUS_ORDER) else set()
    return earlier | next_step


def validate_transition(task: Task, new_status: str, assignee_id_after: int | None) -> None:
    """Raise BusinessRuleError if `task` may not move to `new_status`.

    Why:
        - in_progress needs an owner of the work (an assignee).
        - done is impossible while a blocker is still open.

    Args:
        assignee_id_after: the assignee the task will have after this update
            (the same request may assign and move at once).

    Raises:
        BusinessRuleError: code "invalid_transition", "assignee_required" or "blocked".
    """
    if new_status == task.status:
        return
    if new_status not in allowed_next_statuses(task.status):
        raise BusinessRuleError(
            f"Cannot move a task from {task.status} to {new_status}.",
            code="invalid_transition",
            details={"allowed": sorted(allowed_next_statuses(task.status))},
        )
    # RULE: in_progress requires an assignee.
    if new_status == Status.IN_PROGRESS and assignee_id_after is None:
        raise BusinessRuleError(
            "Assign the task to someone before moving it to in progress.",
            code="assignee_required",
        )
    # RULE: done is rejected while any blocker is not done.
    if new_status == Status.DONE:
        blockers = open_blockers(task.pk)
        if blockers:
            keys = [f"{blocker.project.key}-{blocker.number}" for blocker in blockers]
            raise BusinessRuleError(
                f"Blocked by {', '.join(keys)}. Finish those tasks first.",
                code="blocked",
                details={"blocked_by": keys},
            )


def completed_at_after(new_status: str, old_status: str, current: datetime | None):
    """RULE: entering done stamps completed_at; leaving done clears it."""
    if new_status == Status.DONE and old_status != Status.DONE:
        return clock.now()
    if new_status != Status.DONE:
        return None
    return current
