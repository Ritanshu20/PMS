"""
Server-rendered planning pages: backlog + sprint planning, sprint detail with
the burndown chart, and the POST actions (create, plan, start, complete).
Every action calls `sprints.services`; charts read `sprints.selectors`.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.web import run_service
from projects.permissions import get_membership_or_404, permission_flags
from sprints import selectors, services
from sprints.forms import CompleteSprintForm, SprintForm
from sprints.models import Sprint
from tasks import selectors as task_selectors


@login_required
def backlog_page(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    sprints = list(
        selectors.sprints_for_project(project_id).exclude(status=Sprint.Status.COMPLETED)
    )
    completed = selectors.sprints_for_project(project_id, Sprint.Status.COMPLETED)
    context = {
        "project": membership.project,
        "membership": membership,
        "can": permission_flags(membership),
        "backlog": task_selectors.backlog(project_id).prefetch_related(None),
        "sprints": _with_planned_tasks(project_id, sprints),
        "completed_sprints": completed,
        "sprint_form": SprintForm(),
        "active_tab": "backlog",
    }
    return render(request, "sprints/backlog.html", context)


@login_required
def sprint_detail(request: HttpRequest, project_id: int, sprint_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    sprint = selectors.get_sprint_or_404(project_id, sprint_id)
    velocity = selectors.velocity(project_id)
    planned = list(selectors.sprints_for_project(project_id, Sprint.Status.PLANNED))
    context = {
        "project": membership.project,
        "membership": membership,
        "can": permission_flags(membership),
        "sprint": sprint,
        "tasks": task_selectors.list_tasks(project_id, {"sprint": str(sprint.pk)}).prefetch_related(
            None
        ),
        "burndown_chart": _burndown_chart(sprint),
        "velocity": velocity,
        "velocity_chart": _velocity_chart(velocity),
        "complete_form": CompleteSprintForm(planned_sprints=planned),
        "active_tab": "backlog",
    }
    return render(request, "sprints/sprint_detail.html", context)


def _burndown_chart(sprint: Sprint) -> dict | None:
    """Chart.js-ready series from the burndown selector (None before the sprint starts)."""
    if sprint.status == Sprint.Status.PLANNED:
        return None
    days = selectors.burndown(sprint)["days"]
    return {
        "labels": [day["date"].strftime("%d %b") for day in days],
        "ideal": [day["ideal"] for day in days],
        "remaining": [day["remaining"] for day in days],
    }


def _velocity_chart(velocity: dict) -> dict:
    return {
        "labels": [row["name"] for row in velocity["sprints"]],
        "committed": [row["committed_points"] for row in velocity["sprints"]],
        "completed": [row["completed_points"] for row in velocity["sprints"]],
    }


@login_required
@require_POST
def sprint_create(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    form = SprintForm(request.POST)
    if form.is_valid():
        run_service(
            request,
            lambda: services.create_sprint(membership=membership, **form.cleaned_data),
            "Sprint created.",
        )
    else:
        _report_form_errors(request, form)
    return redirect("web-backlog", project_id=project_id)


@login_required
@require_POST
def sprint_add_task(request: HttpRequest, project_id: int) -> HttpResponse:
    """Plan a backlog task into the sprint chosen in the row's select box."""
    membership = get_membership_or_404(request.user, project_id)
    sprint_id, number = _int(request.POST.get("sprint_id")), _int(request.POST.get("task_number"))
    if sprint_id is None or number is None:
        messages.error(request, "Choose a sprint first.")
    else:
        run_service(
            request,
            lambda: services.add_task_to_sprint(
                membership=membership, sprint_id=sprint_id, task_number=number
            ),
            "Task planned into the sprint.",
        )
    return redirect("web-backlog", project_id=project_id)


@login_required
@require_POST
def sprint_remove_task(request: HttpRequest, project_id: int, sprint_id: int, number: int):
    membership = get_membership_or_404(request.user, project_id)
    run_service(
        request,
        lambda: services.remove_task_from_sprint(
            membership=membership, sprint_id=sprint_id, task_number=number
        ),
        "Task moved back to the backlog.",
    )
    return redirect("web-backlog", project_id=project_id)


@login_required
@require_POST
def sprint_start(request: HttpRequest, project_id: int, sprint_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    ok, _ = run_service(
        request,
        lambda: services.start_sprint(membership=membership, sprint_id=sprint_id),
        "Sprint started.",
    )
    if ok:
        return redirect("web-sprint-detail", project_id=project_id, sprint_id=sprint_id)
    return redirect("web-backlog", project_id=project_id)


@login_required
@require_POST
def sprint_complete(request: HttpRequest, project_id: int, sprint_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    planned = list(selectors.sprints_for_project(project_id, Sprint.Status.PLANNED))
    form = CompleteSprintForm(request.POST, planned_sprints=planned)
    if form.is_valid():
        run_service(
            request,
            lambda: services.complete_sprint(
                membership=membership, sprint_id=sprint_id, **form.cleaned_data
            ),
            "Sprint completed.",
        )
    else:
        _report_form_errors(request, form)
    return redirect("web-sprint-detail", project_id=project_id, sprint_id=sprint_id)


def _with_planned_tasks(project_id: int, sprints: list[Sprint]) -> list[Sprint]:
    """Attach `planned_tasks` to each open sprint, loading all tasks in one query."""
    by_id = {sprint.pk: sprint for sprint in sprints}
    for sprint in sprints:
        sprint.planned_tasks = []
    if by_id:
        # The planning list shows only number/title/status, so skip the label/dependency prefetches.
        tasks = (
            task_selectors.list_tasks(project_id, {})
            .filter(sprint_id__in=list(by_id))
            .prefetch_related(None)
        )
        for task in tasks:
            by_id[task.sprint_id].planned_tasks.append(task)
    return sprints


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _report_form_errors(request: HttpRequest, form) -> None:
    for field_errors in form.errors.values():
        for message in field_errors:
            messages.error(request, message)
