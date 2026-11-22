"""
Write operations for task comments, including @mention notifications.

Called by `tasks/api_views.py` and template views. Permission checks go through
`projects.permissions`; rendering is always autoescaped by templates/DRF, so the
body is stored exactly as typed (never marked safe).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404

from activity.models import Notification
from activity.services import log_activity, notify
from core import clock
from core.exceptions import BusinessRuleError
from projects.models import ProjectMember
from projects.permissions import Action, can_delete_comment, can_edit_comment
from projects.services import lock_project_and_recheck
from tasks.mentions import mentioned_members
from tasks.models import Comment, Task

MAX_COMMENT_LENGTH = 5000
DELETED_PLACEHOLDER = "[deleted]"


def create_comment(*, membership: ProjectMember, task_number: int, body: str) -> Comment:
    """Add a comment and notify mentioned project members.

    Example:
        "@ravi please review" by Asha -> Ravi gets one 'mentioned' notification.
    """
    body = _clean_body(body)
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.COMMENT)
        task = _get_task(actor.project_id, task_number)
        comment = Comment.objects.create(task=task, author=actor.user, body=body)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="comment.created",
            target=task,
            changes={"comment_id": comment.pk},
        )
        _notify_mentions(actor, task, body, already_notified=set())
    return comment


def edit_comment(
    *, membership: ProjectMember, task_number: int, comment_id: int, body: str
) -> Comment:
    """Author edits their comment; only people newly mentioned get notified.

    RULE: one mention notification per comment per user, so editing a comment
    never notifies the same person twice.
    """
    body = _clean_body(body)
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.COMMENT)
        task = _get_task(actor.project_id, task_number)
        comment = _get_comment(task, comment_id)
        # SAFETY: only the author may edit.
        if not can_edit_comment(actor, comment):
            raise PermissionDenied("You can edit only your own comments.")
        if comment.is_deleted:
            raise BusinessRuleError("A deleted comment cannot be edited.", code="comment_deleted")
        before = {
            user.pk for user in mentioned_members(task.project_id, comment.body, actor.user_id)
        }
        comment.body = body
        comment.edited_at = clock.now()
        comment.save(update_fields=["body", "edited_at"])
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="comment.edited",
            target=task,
            changes={"comment_id": comment.pk},
        )
        _notify_mentions(actor, task, body, already_notified=before)
    return comment


def delete_comment(*, membership: ProjectMember, task_number: int, comment_id: int) -> Comment:
    """Soft delete: keep the row, clear the text, show "[deleted]"."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.COMMENT)
        task = _get_task(actor.project_id, task_number)
        comment = _get_comment(task, comment_id)
        # SAFETY: authors delete their own; managers/owners delete any.
        if not can_delete_comment(actor, comment):
            raise PermissionDenied("You can delete only your own comments.")
        comment.is_deleted = True
        comment.body = ""
        comment.save(update_fields=["is_deleted", "body"])
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="comment.deleted",
            target=task,
            changes={"comment_id": comment.pk},
        )
    return comment


def _notify_mentions(actor: ProjectMember, task: Task, body: str, already_notified: set[int]):
    project = actor.project
    for user in mentioned_members(project.pk, body, author_id=actor.user_id):
        if user.pk in already_notified:
            continue
        notify(
            user=user,
            project=project,
            type=Notification.Type.MENTIONED,
            message=f"{actor.user.username} mentioned you on {project.key}-{task.number}: "
            f"{task.title}",
            link=f"/projects/{project.pk}/tasks/{task.number}/",
            task=task,
        )


def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise ValidationError({"body": ["Comment cannot be empty."]})
    if len(body) > MAX_COMMENT_LENGTH:
        raise ValidationError({"body": [f"At most {MAX_COMMENT_LENGTH} characters."]})
    return body


def _get_task(project_id: int, number: int) -> Task:
    task = Task.objects.select_related("project").filter(project_id=project_id, number=number)
    task = task.first()
    if task is None:
        raise Http404("Task not found.")
    return task


def _get_comment(task: Task, comment_id: int) -> Comment:
    # SAFETY: scoped to the task (and therefore the project) in the URL.
    comment = Comment.objects.filter(task=task, pk=comment_id).first()
    if comment is None:
        raise Http404("Comment not found.")
    return comment
