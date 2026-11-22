from django.test import TransactionTestCase, tag

from core import clock
from core.exceptions import BusinessRuleError, VersionConflict
from core.testing import add_member, make_project, make_user, membership_of, run_concurrently
from projects.models import ProjectMember
from tasks import dependency_services, services, time_services
from tasks.models import Task, TaskDependency, TimeEntry


@tag("concurrency")
class TaskNumberingConcurrencyTests(TransactionTestCase):
    """Concurrency test 1."""

    def test_twenty_threads_creating_tasks_get_numbers_1_to_20(self):
        """The project row lock hands out each number exactly once."""
        owner = make_user("owner")
        project = make_project(owner, key="RACE")
        membership = membership_of(owner, project)
        outcomes = run_concurrently(
            [
                lambda index=index: services.create_task(
                    membership=membership, title=f"task {index}"
                )
                for index in range(20)
            ]
        )
        self.assertTrue(all(kind == "ok" for kind, _ in outcomes), outcomes)
        numbers = sorted(Task.objects.filter(project=project).values_list("number", flat=True))
        self.assertEqual(numbers, list(range(1, 21)))
        project.refresh_from_db()
        self.assertEqual(project.task_counter, 20)


@tag("concurrency")
class OptimisticLockingConcurrencyTests(TransactionTestCase):
    """Concurrency test 5."""

    def test_two_updates_with_same_version_exactly_one_wins(self):
        """One update applies, the other gets VersionConflict; nothing is lost silently."""
        owner = make_user("owner")
        editor = make_user("editor")
        project = make_project(owner, key="RACE")
        add_member(project, editor, ProjectMember.Role.MEMBER)
        task = services.create_task(membership=membership_of(owner, project), title="original")
        owner_m = membership_of(owner, project)
        editor_m = membership_of(editor, project)
        outcomes = run_concurrently(
            [
                lambda: services.update_task(
                    membership=owner_m,
                    number=task.number,
                    expected_version=1,
                    changes={"title": "from owner"},
                ),
                lambda: services.update_task(
                    membership=editor_m,
                    number=task.number,
                    expected_version=1,
                    changes={"title": "from editor"},
                ),
            ]
        )
        winners = [result for kind, result in outcomes if kind == "ok"]
        losers = [result for kind, result in outcomes if kind == "error"]
        self.assertEqual(len(winners), 1, outcomes)
        self.assertEqual(len(losers), 1, outcomes)
        self.assertIsInstance(losers[0], VersionConflict)
        task.refresh_from_db()
        self.assertEqual(task.version, 2)
        self.assertEqual(task.title, winners[0].title)


@tag("concurrency")
class DependencyConcurrencyTests(TransactionTestCase):
    """Concurrency test 2."""

    def test_opposite_dependencies_at_same_time_only_one_succeeds(self):
        """A blocks B and B blocks A in parallel: the lock lets one in, the other is a cycle."""
        owner = make_user("owner")
        project = make_project(owner, key="RACE")
        membership = membership_of(owner, project)
        task_a = services.create_task(membership=membership, title="A")
        task_b = services.create_task(membership=membership, title="B")
        outcomes = run_concurrently(
            [
                lambda: dependency_services.add_dependency(
                    membership=membership,
                    blocker_number=task_a.number,
                    blocked_number=task_b.number,
                ),
                lambda: dependency_services.add_dependency(
                    membership=membership,
                    blocker_number=task_b.number,
                    blocked_number=task_a.number,
                ),
            ]
        )
        successes = [result for kind, result in outcomes if kind == "ok"]
        errors = [result for kind, result in outcomes if kind == "error"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertIsInstance(errors[0], BusinessRuleError)
        self.assertEqual(errors[0].code, "dependency_cycle")
        self.assertEqual(TaskDependency.objects.filter(project=project).count(), 1)


@tag("concurrency")
class DailyTimeLimitConcurrencyTests(TransactionTestCase):
    """Extra: the 1440-minute daily cap holds under parallel requests."""

    def test_two_parallel_800_minute_entries_only_one_is_saved(self):
        """The user-row lock makes the second entry see the first: 800 + 800 > 1440."""
        user = make_user("worker")
        project_a = make_project(user, key="AAA")
        project_b = make_project(user, key="BBB")
        task_a = services.create_task(membership=membership_of(user, project_a), title="a")
        task_b = services.create_task(membership=membership_of(user, project_b), title="b")
        today = clock.today()
        outcomes = run_concurrently(
            [
                lambda: time_services.log_time(
                    membership=membership_of(user, project_a),
                    task_number=task_a.number,
                    minutes=800,
                    date=today,
                ),
                lambda: time_services.log_time(
                    membership=membership_of(user, project_b),
                    task_number=task_b.number,
                    minutes=800,
                    date=today,
                ),
            ]
        )
        errors = [result for kind, result in outcomes if kind == "error"]
        self.assertEqual(len(errors), 1, outcomes)
        self.assertEqual(errors[0].code, "daily_limit")
        self.assertEqual(TimeEntry.objects.filter(user=user).count(), 1)
