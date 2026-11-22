"""
Write operations for time tracking.

Called by `tasks/api_views.py` and template views. Rules:
- integer minutes, 1..1440 per entry, no future dates,
- a user's total per day across ALL projects is at most 1440 minutes,
- viewers cannot log time; users change only their own entries; managers and
  owners may delete any entry in their project.
"""

from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import Http404

from accounts.models import User
from activity.services import log_activity
from core import clock
from core.exceptions import BusinessRuleError
from projects.models import ProjectMember
from projects.permissions import Action, can_change_time_entry
from projects.services import lock_project_and_recheck
from tasks.models import Task, TimeEntry

MINUTES_PER_DAY = 1440


def log_time(
    *, membership: ProjectMember, task_number: int, minutes: int, date: date, note: str = ""
) -> TimeEntry:
    """Record time the acting user spent on a task.

    Example: 600 min already logged on 10 Mar; adding 900 -> 1500 > 1440 -> rejected.

    Raises:
        ValidationError: minutes outside 1..1440 or a future date.
        BusinessRuleError: daily total would exceed 1440 minutes (code "daily_limit").
    """
    _validate_minutes_and_date(minutes, date)
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.LOG_OWN_TIME)
        task = _get_task(actor.project_id, task_number)
        _ensure_daily_limit(actor.user_id, date, minutes)
        entry = TimeEntry.objects.create(
            task=task, user=actor.user, minutes=minutes, date=date, note=note
        )
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="time.logged",
            target=task,
            changes={"entry_id": entry.pk, "minutes": minutes, "date": date},
        )
    return entry


def update_time_entry(
    *,
    membership: ProjectMember,
    entry_id: int,
    minutes: int | None = None,
    date: date | None = None,
    note: str | None = None,
) -> TimeEntry:
    """Change one of your own entries; the daily cap is re-checked."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.LOG_OWN_TIME)
        entry = _get_entry(actor.project_id, entry_id)
        # SAFETY: only your own entries can be edited.
        if not can_change_time_entry(actor, entry):
            raise PermissionDenied("You can edit only your own time entries.")
        new_minutes = entry.minutes if minutes is None else minutes
        new_date = entry.date if date is None else date
        _validate_minutes_and_date(new_minutes, new_date)
        _ensure_daily_limit(entry.user_id, new_date, new_minutes, exclude_entry_id=entry.pk)
        entry.minutes, entry.date = new_minutes, new_date
        entry.note = entry.note if note is None else note
        entry.save(update_fields=["minutes", "date", "note"])
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="time.updated",
            target=entry.task,
            changes={"entry_id": entry.pk, "minutes": new_minutes, "date": new_date},
        )
    return entry


def delete_time_entry(*, membership: ProjectMember, entry_id: int) -> None:
    """Delete your own entry; managers/owners may delete anyone's."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.LOG_OWN_TIME)
        entry = _get_entry(actor.project_id, entry_id)
        # SAFETY: own entry, or a manager/owner cleaning up.
        if not can_change_time_entry(actor, entry, deleting=True):
            raise PermissionDenied("You can delete only your own time entries.")
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="time.deleted",
            target=entry.task,
            changes={"entry_id": entry.pk, "minutes": entry.minutes, "user_id": entry.user_id},
        )
        entry.delete()


def _ensure_daily_limit(
    user_id: int, day: date, minutes: int, exclude_entry_id: int | None = None
) -> None:
    """RULE: total minutes per user per day across all projects <= 1440.

    RACE: the user's row is locked first, so two entries for the same user
    (even in two different projects) are checked one after the other.
    """
    User.objects.select_for_update().filter(pk=user_id).first()
    existing = TimeEntry.objects.filter(user_id=user_id, date=day)
    if exclude_entry_id is not None:
        existing = existing.exclude(pk=exclude_entry_id)
    already = existing.aggregate(total=Coalesce(Sum("minutes"), 0))["total"]
    if already + minutes > MINUTES_PER_DAY:
        raise BusinessRuleError(
            f"You already logged {already} minutes on {day.isoformat()}; "
            f"a day has at most {MINUTES_PER_DAY}.",
            code="daily_limit",
        )


def _validate_minutes_and_date(minutes: int, day: date) -> None:
    errors = {}
    if not TimeEntry.MIN_MINUTES <= minutes <= TimeEntry.MAX_MINUTES:
        errors["minutes"] = ["Minutes must be between 1 and 1440."]
    if day > clock.today():
        errors["date"] = ["Time cannot be logged for a future date."]
    if errors:
        raise ValidationError(errors)


def _get_task(project_id: int, number: int) -> Task:
    task = Task.objects.filter(project_id=project_id, number=number).first()
    if task is None:
        raise Http404("Task not found.")
    return task


def _get_entry(project_id: int, entry_id: int) -> TimeEntry:
    # SAFETY: scoped to the project in the URL (no IDOR across projects).
    entry = (
        TimeEntry.objects.select_related("task")
        .filter(task__project_id=project_id, pk=entry_id)
        .first()
    )
    if entry is None:
        raise Http404("Time entry not found.")
    return entry
