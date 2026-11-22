from datetime import date

from django.test import TransactionTestCase, tag

from core.exceptions import BusinessRuleError
from core.testing import make_project, make_user, membership_of, run_concurrently
from sprints import services
from sprints.models import Sprint


@tag("concurrency")
class SprintStartConcurrencyTests(TransactionTestCase):
    """Concurrency test 3."""

    def test_two_threads_starting_two_sprints_only_one_becomes_active(self):
        """The project lock (and the partial unique index) allow a single active sprint."""
        owner = make_user("owner")
        project = make_project(owner, key="RACE")
        membership = membership_of(owner, project)
        sprints = [
            Sprint.objects.create(
                project=project,
                name=name,
                start_date=date(2026, 3, 2),
                end_date=date(2026, 3, 13),
            )
            for name in ("A", "B")
        ]
        outcomes = run_concurrently(
            [
                lambda sprint=sprint: services.start_sprint(
                    membership=membership, sprint_id=sprint.pk
                )
                for sprint in sprints
            ]
        )
        successes = [result for kind, result in outcomes if kind == "ok"]
        errors = [result for kind, result in outcomes if kind == "error"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertIsInstance(errors[0], BusinessRuleError)
        self.assertEqual(errors[0].code, "sprint_already_active")
        self.assertEqual(Sprint.objects.filter(project=project, status="active").count(), 1)
