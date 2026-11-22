"""
Write operations for projects and memberships.

Called by `projects/api_views.py` (and later template views). Every function:
- takes the acting user's `membership` (already proven to exist by
  `permissions.get_membership_or_404`),
- locks the project row, re-reads the actor's membership and re-checks
  permission (roles may have changed since the request started),
- writes its ActivityLog row in the same transaction.
Must never skip `permissions.require_permission`.
"""

import logging
import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404

from accounts.models import User
from activity.services import log_activity
from core.exceptions import BusinessRuleError
from projects.models import Project, ProjectMember
from projects.permissions import Action, Role, can_manage_member, require_permission

logger = logging.getLogger(__name__)

PROJECT_KEY_PATTERN = re.compile(r"^[A-Z]{2,6}$")


def lock_project_and_recheck(membership: ProjectMember, action: str) -> ProjectMember:
    """Lock the project row and return a fresh copy of the actor's membership.

    Why: two requests touching the same project (task numbering, member changes,
    dependency graph) must run one after another. Re-reading the membership
    after the lock means a role removed a moment ago is respected.

    Raises:
        Http404: the actor is no longer a member.
        PermissionDenied: the actor's current role does not allow `action`.
    """
    # RACE: SELECT ... FOR UPDATE serialises all writers of this project.
    Project.objects.select_for_update().get(pk=membership.project_id)
    fresh = (
        ProjectMember.objects.select_related("project", "user")
        .filter(pk=membership.pk, user_id=membership.user_id)
        .first()
    )
    if fresh is None:
        raise Http404("Project not found.")
    require_permission(fresh, action)
    return fresh


def normalize_project_key(raw_key: str) -> str:
    """Uppercase and validate a project key ("web" -> "WEB").

    Raises:
        ValidationError: key is not 2-6 letters A-Z.
    """
    key = (raw_key or "").strip().upper()
    if not PROJECT_KEY_PATTERN.match(key):
        raise ValidationError({"key": ["Key must be 2 to 6 letters A-Z, for example WEB."]})
    return key


def create_project(*, user: User, name: str, key: str, description: str = "") -> Project:
    """Create a project; the creator becomes its first owner.

    Raises:
        ValidationError: invalid or already-used key, empty name.
    """
    key = normalize_project_key(key)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": ["Name is required."]})
    if Project.objects.filter(key=key).exists():
        raise ValidationError({"key": ["This key is already used by another project."]})
    try:
        with transaction.atomic():
            project = Project.objects.create(name=name, key=key, description=description)
            ProjectMember.objects.create(project=project, user=user, role=Role.OWNER)
            log_activity(
                project=project,
                actor=user,
                verb="project.created",
                target=project,
                changes={"name": name, "key": key},
            )
    except IntegrityError as exc:
        # RACE: two users creating the same key at once; the unique index decides.
        raise ValidationError({"key": ["This key is already used by another project."]}) from exc
    return project


def update_project(
    *, membership: ProjectMember, name: str | None = None, description: str | None = None
) -> Project:
    """Rename / re-describe a project (managers and owners)."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.EDIT_PROJECT)
        project = actor.project
        changes = {}
        if name is not None:
            if not name.strip():
                raise ValidationError({"name": ["Name is required."]})
            changes["name"] = [project.name, name.strip()]
            project.name = name.strip()
        if description is not None:
            changes["description"] = [project.description, description]
            project.description = description
        project.save()
        log_activity(
            project=project,
            actor=actor.user,
            verb="project.updated",
            target=project,
            changes=changes,
        )
    return project


def archive_project(*, membership: ProjectMember) -> Project:
    """Make the project read-only (owners only)."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.ARCHIVE_PROJECT)
        project = actor.project
        project.status = Project.Status.ARCHIVED
        project.save(update_fields=["status", "updated_at"])
        log_activity(project=project, actor=actor.user, verb="project.archived", target=project)
    return project


def delete_project(*, membership: ProjectMember) -> None:
    """Delete the project and everything in it (owners only)."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.DELETE_PROJECT)
        project_id = actor.project_id
        actor.project.delete()
    logger.info("project_deleted project_id=%s actor_id=%s", project_id, actor.user_id)


def add_member(*, membership: ProjectMember, email: str, role: str) -> ProjectMember:
    """Add a registered user to the project by exact email.

    Raises:
        ValidationError: unknown role, no user with that email, already a member.
        PermissionDenied: a manager tries to add an owner.
    """
    _validate_role(role)
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_MEMBERS)
        # SAFETY: managers may not create owners.
        if not can_manage_member(actor, target_role="", new_role=role):
            raise PermissionDenied("Only owners can add owners.")
        user = User.objects.filter(email__iexact=(email or "").strip()).first()
        if user is None:
            raise ValidationError({"email": ["No registered user has this email."]})
        if ProjectMember.objects.filter(project=actor.project, user=user).exists():
            raise ValidationError({"email": ["This user is already a member."]})
        member = ProjectMember.objects.create(project=actor.project, user=user, role=role)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="member.added",
            target=member,
            changes={"user_id": user.pk, "role": role},
        )
    return member


def change_member_role(
    *, membership: ProjectMember, member_id: int, new_role: str
) -> ProjectMember:
    """Change a member's role, protecting the last owner.

    Raises:
        Http404: member_id is not in this project.
        PermissionDenied: manager touching an owner / granting owner.
        BusinessRuleError: would leave the project without an owner.
    """
    _validate_role(new_role)
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_MEMBERS)
        target = _get_member_in_project(actor.project_id, member_id)
        if not can_manage_member(actor, target_role=target.role, new_role=new_role):
            raise PermissionDenied("Managers cannot change owners or grant the owner role.")
        if target.role == Role.OWNER and new_role != Role.OWNER:
            _ensure_another_owner_exists(target)
        old_role = target.role
        target.role = new_role
        target.save(update_fields=["role"])
        unassigned = 0
        if new_role == Role.VIEWER:
            unassigned = _unassign_open_tasks(target)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="member.role_changed",
            target=target,
            changes={"role": [old_role, new_role], "unassigned_tasks": unassigned},
        )
    return target


def remove_member(*, membership: ProjectMember, member_id: int) -> None:
    """Remove a member, protecting the last owner. Their tasks become unassigned."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_MEMBERS)
        target = _get_member_in_project(actor.project_id, member_id)
        if not can_manage_member(actor, target_role=target.role, new_role=None):
            raise PermissionDenied("Managers cannot remove owners.")
        if target.role == Role.OWNER:
            _ensure_another_owner_exists(target)
        unassigned = _unassign_open_tasks(target)
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="member.removed",
            target=target,
            changes={"user_id": target.user_id, "unassigned_tasks": unassigned},
        )
        target.delete()


def transfer_ownership(*, membership: ProjectMember, member_id: int) -> ProjectMember:
    """Make another member an owner and step the acting owner down to manager."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.TRANSFER_OWNERSHIP)
        target = _get_member_in_project(actor.project_id, member_id)
        if target.pk == actor.pk:
            raise BusinessRuleError("You already own this project.", code="invalid_transfer")
        target.role = Role.OWNER
        target.save(update_fields=["role"])
        actor.role = Role.MANAGER
        actor.save(update_fields=["role"])
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="project.ownership_transferred",
            target=actor.project,
            changes={"from_user_id": actor.user_id, "to_user_id": target.user_id},
        )
    return target


def _validate_role(role: str) -> None:
    if role not in Role.values:
        raise ValidationError({"role": [f"Role must be one of: {', '.join(Role.values)}."]})


def _get_member_in_project(project_id: int, member_id: int) -> ProjectMember:
    # SAFETY: scoped to the actor's project, so another project's member id gives 404.
    target = (
        ProjectMember.objects.select_related("user")
        .filter(project_id=project_id, pk=member_id)
        .first()
    )
    if target is None:
        raise Http404("Member not found.")
    return target


def _ensure_another_owner_exists(target: ProjectMember) -> None:
    # RULE + RACE: runs while the project row is locked, so two owners demoting
    # each other at the same time cannot both pass this check.
    other_owners = ProjectMember.objects.filter(
        project_id=target.project_id, role=Role.OWNER
    ).exclude(pk=target.pk)
    if not other_owners.exists():
        raise BusinessRuleError(
            "A project must keep at least one owner. Add or promote another owner first.",
            code="last_owner",
        )


def _unassign_open_tasks(target: ProjectMember) -> int:
    """Clear `assignee` on the target's tasks so the 'assignee is a member' rule holds."""
    from tasks.models import Task

    # RULE: version is bumped so open editors get a 409 instead of overwriting.
    return Task.objects.filter(project_id=target.project_id, assignee_id=target.user_id).update(
        assignee=None, version=F("version") + 1
    )
