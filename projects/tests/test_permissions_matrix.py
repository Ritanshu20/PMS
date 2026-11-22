from django.test import TestCase

from core.testing import ProjectWithRolesMixin, make_user, membership_of
from projects.models import Project
from projects.permissions import (
    Action,
    can_delete_task,
    can_manage_member,
    get_membership_or_404,
    has_permission,
)
from tasks.models import Task

# Written out by hand from the spec table, independent of permissions.py.
#                                viewer member manager owner
EXPECTED_MATRIX = {
    Action.VIEW_PROJECT:        (True,  True,  True,  True),
    Action.EDIT_PROJECT:        (False, False, True,  True),
    Action.CREATE_TASK:         (False, True,  True,  True),
    Action.EDIT_TASK:           (False, True,  True,  True),
    Action.CHANGE_TASK_STATUS:  (False, True,  True,  True),
    Action.DELETE_OWN_TASK:     (False, True,  True,  True),
    Action.DELETE_ANY_TASK:     (False, False, True,  True),
    Action.COMMENT:             (False, True,  True,  True),
    Action.MODERATE_COMMENTS:   (False, False, True,  True),
    Action.LOG_OWN_TIME:        (False, True,  True,  True),
    Action.DELETE_ANY_TIME_ENTRY: (False, False, True, True),
    Action.MANAGE_DEPENDENCIES: (False, True,  True,  True),
    Action.MANAGE_SPRINTS:      (False, False, True,  True),
    Action.MANAGE_LABELS:       (False, False, True,  True),
    Action.MANAGE_MEMBERS:      (False, False, True,  True),
    Action.ARCHIVE_PROJECT:     (False, False, False, True),
    Action.DELETE_PROJECT:      (False, False, False, True),
    Action.TRANSFER_OWNERSHIP:  (False, False, False, True),
}  # fmt: skip
ROLES_IN_ORDER = ("viewer", "member", "manager", "owner")


class PermissionMatrixTests(ProjectWithRolesMixin, TestCase):
    def test_every_cell_of_the_permission_matrix(self):
        """Each role gets exactly the actions in the spec table, nothing more."""
        self.assertEqual(set(EXPECTED_MATRIX), set(Action.values))
        for role_index, role in enumerate(ROLES_IN_ORDER):
            membership = membership_of(self.users_by_role[role], self.project)
            for action, expected in EXPECTED_MATRIX.items():
                with self.subTest(role=role, action=action):
                    self.assertEqual(has_permission(membership, action), expected[role_index])

    def test_archived_project_allows_only_view_and_delete(self):
        """Archived projects are read-only for everyone; the owner may still delete."""
        self.project.status = Project.Status.ARCHIVED
        self.project.save()
        owner = membership_of(self.owner, self.project)
        for action in Action.values:
            with self.subTest(action=action):
                expected = action in {Action.VIEW_PROJECT, Action.DELETE_PROJECT}
                self.assertEqual(has_permission(owner, action), expected)

    def test_member_can_delete_only_tasks_they_reported(self):
        """Members delete own tasks; managers and owners delete any task."""
        own = Task.objects.create(project=self.project, number=1, title="a", reporter=self.member)
        other = Task.objects.create(project=self.project, number=2, title="b", reporter=self.owner)
        member = membership_of(self.member, self.project)
        manager = membership_of(self.manager, self.project)
        viewer = membership_of(self.viewer, self.project)
        self.assertTrue(can_delete_task(member, own))
        self.assertFalse(can_delete_task(member, other))
        self.assertTrue(can_delete_task(manager, other))
        self.assertFalse(can_delete_task(viewer, own))

    def test_managers_cannot_touch_owners_or_grant_owner(self):
        """Managers manage members but owners are out of their reach."""
        manager = membership_of(self.manager, self.project)
        owner = membership_of(self.owner, self.project)
        self.assertTrue(can_manage_member(manager, target_role="member", new_role="viewer"))
        self.assertTrue(can_manage_member(manager, target_role="member", new_role="manager"))
        self.assertFalse(can_manage_member(manager, target_role="owner", new_role="member"))
        self.assertFalse(can_manage_member(manager, target_role="member", new_role="owner"))
        self.assertFalse(can_manage_member(manager, target_role="owner", new_role=None))
        self.assertTrue(can_manage_member(owner, target_role="owner", new_role="manager"))
        member = membership_of(self.member, self.project)
        self.assertFalse(can_manage_member(member, target_role="viewer", new_role="member"))

    def test_non_member_and_unknown_project_both_give_404(self):
        """Project existence is never leaked to outsiders."""
        from django.http import Http404

        with self.assertRaises(Http404):
            get_membership_or_404(self.outsider, self.project.pk)
        with self.assertRaises(Http404):
            get_membership_or_404(make_user("nobody"), 999_999)
