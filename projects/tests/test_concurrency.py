from django.core.exceptions import PermissionDenied
from django.test import TransactionTestCase, tag

from core.exceptions import BusinessRuleError
from core.testing import add_member, make_project, make_user, membership_of, run_concurrently
from projects import services
from projects.models import ProjectMember

Role = ProjectMember.Role


@tag("concurrency")
class LastOwnerConcurrencyTests(TransactionTestCase):
    """Concurrency test 4: owners demoting/removing each other at the same instant."""

    def setUp(self) -> None:
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.project = make_project(self.alice, key="RACE")
        add_member(self.project, self.bob, Role.OWNER)

    def assert_exactly_one_succeeded_and_an_owner_remains(self, outcomes):
        successes = [o for o in outcomes if o[0] == "ok"]
        failures = [o[1] for o in outcomes if o[0] == "error"]
        self.assertEqual(len(successes), 1, outcomes)
        for error in failures:
            self.assertIsInstance(error, PermissionDenied | BusinessRuleError)
        owners = ProjectMember.objects.filter(project=self.project, role=Role.OWNER).count()
        self.assertGreaterEqual(owners, 1)

    def test_two_owners_demoting_each_other_leave_at_least_one_owner(self):
        """The project lock serialises the two demotions; the second is refused."""
        alice_m = membership_of(self.alice, self.project)
        bob_m = membership_of(self.bob, self.project)
        outcomes = run_concurrently(
            [
                lambda: services.change_member_role(
                    membership=alice_m, member_id=bob_m.pk, new_role=Role.MANAGER
                ),
                lambda: services.change_member_role(
                    membership=bob_m, member_id=alice_m.pk, new_role=Role.MANAGER
                ),
            ]
        )
        self.assert_exactly_one_succeeded_and_an_owner_remains(outcomes)

    def test_two_owners_removing_each_other_leave_at_least_one_owner(self):
        """Same race with removal instead of demotion."""
        alice_m = membership_of(self.alice, self.project)
        bob_m = membership_of(self.bob, self.project)
        outcomes = run_concurrently(
            [
                lambda: services.remove_member(membership=alice_m, member_id=bob_m.pk),
                lambda: services.remove_member(membership=bob_m, member_id=alice_m.pk),
            ]
        )
        successes = [o for o in outcomes if o[0] == "ok"]
        self.assertEqual(len(successes), 1, outcomes)
        owners = ProjectMember.objects.filter(project=self.project, role=Role.OWNER).count()
        self.assertEqual(owners, 1)
