"""
The ONE place that answers "what can this user do in this project?".

Every API view, template view, selector and export goes through here.
Rules:
- A non-member gets `Http404` for anything in a project (existence is never leaked).
- A member without the right gets `PermissionDenied` (403).
- An archived project is read-only (only viewing and deleting it are allowed).
This module must never write to the database.
"""

from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import Http404

from projects.models import ProjectMember

Role = ProjectMember.Role


class Action(models.TextChoices):
    VIEW_PROJECT = "view_project"
    EDIT_PROJECT = "edit_project"
    CREATE_TASK = "create_task"
    EDIT_TASK = "edit_task"
    CHANGE_TASK_STATUS = "change_task_status"
    DELETE_OWN_TASK = "delete_own_task"
    DELETE_ANY_TASK = "delete_any_task"
    COMMENT = "comment"
    MODERATE_COMMENTS = "moderate_comments"
    LOG_OWN_TIME = "log_own_time"
    DELETE_ANY_TIME_ENTRY = "delete_any_time_entry"
    MANAGE_DEPENDENCIES = "manage_dependencies"
    MANAGE_SPRINTS = "manage_sprints"
    MANAGE_LABELS = "manage_labels"
    MANAGE_MEMBERS = "manage_members"
    ARCHIVE_PROJECT = "archive_project"
    DELETE_PROJECT = "delete_project"
    TRANSFER_OWNERSHIP = "transfer_ownership"


_VIEWER_ACTIONS = {Action.VIEW_PROJECT}
_MEMBER_ACTIONS = _VIEWER_ACTIONS | {
    Action.CREATE_TASK,
    Action.EDIT_TASK,
    Action.CHANGE_TASK_STATUS,
    Action.DELETE_OWN_TASK,
    Action.COMMENT,
    Action.LOG_OWN_TIME,
    Action.MANAGE_DEPENDENCIES,
}
_MANAGER_ACTIONS = _MEMBER_ACTIONS | {
    Action.EDIT_PROJECT,
    Action.DELETE_ANY_TASK,
    Action.MODERATE_COMMENTS,
    Action.DELETE_ANY_TIME_ENTRY,
    Action.MANAGE_SPRINTS,
    Action.MANAGE_LABELS,
    Action.MANAGE_MEMBERS,
}
_OWNER_ACTIONS = _MANAGER_ACTIONS | {
    Action.ARCHIVE_PROJECT,
    Action.DELETE_PROJECT,
    Action.TRANSFER_OWNERSHIP,
}

# RULE: the permission matrix from the spec, in one table.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.VIEWER: frozenset(_VIEWER_ACTIONS),
    Role.MEMBER: frozenset(_MEMBER_ACTIONS),
    Role.MANAGER: frozenset(_MANAGER_ACTIONS),
    Role.OWNER: frozenset(_OWNER_ACTIONS),
}

# RULE: the only actions still allowed on an archived project.
ARCHIVED_ALLOWED_ACTIONS = frozenset({Action.VIEW_PROJECT, Action.DELETE_PROJECT})

# RULE: roles that may be assigned tasks (viewers cannot work on tasks).
ASSIGNABLE_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.MEMBER})


def get_membership_or_404(user, project_id: int) -> ProjectMember:
    """Return the user's membership (with its project loaded) or raise Http404.

    Why: non-members must not learn whether a project exists, so "not a member"
    and "no such project" give the same 404.

    Raises:
        Http404: anonymous user, unknown project, or user is not a member.
    """
    # SAFETY: the lookup is scoped to the requesting user, so no IDOR is possible.
    if not user.is_authenticated:
        raise Http404("Project not found.")
    membership = (
        ProjectMember.objects.select_related("project", "user")
        .filter(project_id=project_id, user=user)
        .first()
    )
    if membership is None:
        raise Http404("Project not found.")
    return membership


def has_permission(membership: ProjectMember, action: str) -> bool:
    """True if this member's role allows `action` in the member's project."""
    if membership.project.is_archived and action not in ARCHIVED_ALLOWED_ACTIONS:
        return False
    return action in ROLE_PERMISSIONS[membership.role]


def permission_flags(membership: ProjectMember) -> dict[str, bool]:
    """{action: allowed} for every action, so templates can show/hide buttons.

    Example: in a template, `{% if can.create_task %}` shows the "New task" button.
    Hiding a button is only convenience; the service still checks on submit.
    """
    return {action: has_permission(membership, action) for action in Action.values}


def require_permission(membership: ProjectMember, action: str) -> None:
    """Raise PermissionDenied (403) unless the member may perform `action`."""
    # SAFETY: single gate used by every write path.
    if not has_permission(membership, action):
        if membership.project.is_archived:
            raise PermissionDenied("This project is archived and read-only.")
        raise PermissionDenied("Your project role does not allow this action.")


def can_delete_task(membership: ProjectMember, task) -> bool:
    """Managers/owners delete any task; members delete only tasks they reported."""
    if has_permission(membership, Action.DELETE_ANY_TASK):
        return True
    return has_permission(membership, Action.DELETE_OWN_TASK) and task.reporter_id == (
        membership.user_id
    )


def can_edit_comment(membership: ProjectMember, comment) -> bool:
    """Only the author edits a comment (and only while they may still comment)."""
    return has_permission(membership, Action.COMMENT) and comment.author_id == membership.user_id


def can_delete_comment(membership: ProjectMember, comment) -> bool:
    """Authors delete their own comments; managers and owners delete any."""
    if has_permission(membership, Action.MODERATE_COMMENTS):
        return True
    return can_edit_comment(membership, comment)


def can_change_time_entry(membership: ProjectMember, entry, deleting: bool = False) -> bool:
    """Users edit/delete only their own entries; managers and owners may delete any."""
    if deleting and has_permission(membership, Action.DELETE_ANY_TIME_ENTRY):
        return True
    return has_permission(membership, Action.LOG_OWN_TIME) and entry.user_id == membership.user_id


def can_manage_member(actor: ProjectMember, target_role: str, new_role: str | None) -> bool:
    """Can `actor` change a member from `target_role` to `new_role` (None = remove)?

    Why: managers manage members but must never touch owners or create owners.
    Owners can do anything (last-owner protection is enforced in the service,
    under a row lock, because it depends on other rows).
    """
    if not has_permission(actor, Action.MANAGE_MEMBERS):
        return False
    if actor.role == Role.OWNER:
        return True
    return target_role != Role.OWNER and new_role != Role.OWNER


def is_assignable(membership: ProjectMember | None) -> bool:
    return membership is not None and membership.role in ASSIGNABLE_ROLES


def visible_projects_filter(user, project_field: str = "pk") -> models.Q:
    """Q filter for rows whose project the user can see (used by selectors/list views).

    `project_field` is "pk" when filtering projects, "project_id" for tasks etc.
    A subquery (not a join on `members`) so later annotations such as
    Count("members") still count every member, not only this user's row.
    """
    my_project_ids = ProjectMember.objects.filter(user=user).values("project_id")
    return models.Q(**{f"{project_field}__in": my_project_ids})
