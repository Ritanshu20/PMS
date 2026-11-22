"""
Task dependencies: "blocker must be done before blocked can be done".

Called by `tasks/api_views.py`. All checks run while the project row is
locked, so two concurrent requests can never *together* create a cycle
(A blocks B and B blocks A at the same instant): the second request waits,
then sees the first edge and is rejected.
"""

from collections import deque

from django.db import transaction
from django.http import Http404

from activity.services import log_activity
from core.exceptions import BusinessRuleError
from projects.models import ProjectMember
from projects.permissions import Action
from projects.services import lock_project_and_recheck
from tasks.models import Task, TaskDependency
from tasks.selectors import dependency_graph


def would_create_cycle(graph: dict[int, set[int]], blocker_id: int, blocked_id: int) -> bool:
    """True if adding the edge blocker -> blocked would close a loop.

    Why: a cycle (A blocks B blocks C blocks A) means none of them can ever be done.

    How: breadth-first search from `blocked` along existing "blocks" edges.
    If we can already reach `blocker`, the new edge would close a loop.

    Example:
        graph = {A: {B}, B: {C}}           # A blocks B, B blocks C
        would_create_cycle(graph, C, A)    # C blocks A? A->B->C reaches C -> True
        would_create_cycle(graph, A, C)    # A blocks C? from C nothing reachable -> False
    """
    if blocker_id == blocked_id:
        return True
    seen = {blocked_id}
    queue = deque([blocked_id])
    while queue:
        current = queue.popleft()
        for next_id in graph.get(current, set()):
            if next_id == blocker_id:
                return True
            if next_id not in seen:
                seen.add(next_id)
                queue.append(next_id)
    return False


def add_dependency(
    *, membership: ProjectMember, blocker_number: int, blocked_number: int
) -> TaskDependency:
    """Record that task `blocker_number` blocks task `blocked_number` (same project).

    Raises:
        Http404: either task is not in this project (cross-project is impossible
            because both numbers are looked up inside the actor's project).
        BusinessRuleError: self-dependency, duplicate, cycle, or a done task
            getting a new open blocker.
    """
    with transaction.atomic():
        # RACE: project lock makes the graph read + insert one atomic step.
        actor = lock_project_and_recheck(membership, Action.MANAGE_DEPENDENCIES)
        blocker = _get_task(actor.project_id, blocker_number)
        blocked = _get_task(actor.project_id, blocked_number)
        _validate_new_edge(actor.project_id, blocker, blocked)
        dependency = TaskDependency.objects.create(
            project=actor.project, blocker=blocker, blocked=blocked
        )
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="dependency.added",
            target=blocked,
            changes={"blocker": blocker.number, "blocked": blocked.number},
        )
    return dependency


def remove_dependency(
    *, membership: ProjectMember, blocker_number: int, blocked_number: int
) -> None:
    """Delete the edge blocker -> blocked. Raises Http404 if it does not exist."""
    with transaction.atomic():
        actor = lock_project_and_recheck(membership, Action.MANAGE_DEPENDENCIES)
        dependency = (
            TaskDependency.objects.select_related("blocked")
            .filter(
                project_id=actor.project_id,
                blocker__number=blocker_number,
                blocked__number=blocked_number,
            )
            .first()
        )
        if dependency is None:
            raise Http404("Dependency not found.")
        log_activity(
            project=actor.project,
            actor=actor.user,
            verb="dependency.removed",
            target=dependency.blocked,
            changes={"blocker": blocker_number, "blocked": blocked_number},
        )
        dependency.delete()


def _validate_new_edge(project_id: int, blocker: Task, blocked: Task) -> None:
    # RULE: no self-dependency.
    if blocker.pk == blocked.pk:
        raise BusinessRuleError("A task cannot block itself.", code="self_dependency")
    # RULE: no duplicates.
    if TaskDependency.objects.filter(blocker=blocker, blocked=blocked).exists():
        raise BusinessRuleError("This dependency already exists.", code="duplicate_dependency")
    # RULE: a done task must not get an open blocker (it would break the done rule).
    if blocked.status == Task.Status.DONE and blocker.status != Task.Status.DONE:
        raise BusinessRuleError(
            "This task is already done. Reopen it before adding an open blocker.",
            code="blocked_task_done",
        )
    # RULE: no cycles, however long.
    if would_create_cycle(dependency_graph(project_id), blocker.pk, blocked.pk):
        raise BusinessRuleError(
            f"#{blocker.number} cannot block #{blocked.number}: that would create a cycle.",
            code="dependency_cycle",
        )


def _get_task(project_id: int, number: int) -> Task:
    # SAFETY: scoped to the actor's project.
    task = Task.objects.filter(project_id=project_id, number=number).first()
    if task is None:
        raise Http404("Task not found.")
    return task
