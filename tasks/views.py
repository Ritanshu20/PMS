"""
Server-rendered task pages: kanban board, task detail, create/edit/delete,
plus the JSON endpoint the board's drag-and-drop calls.

Pages use session auth + CSRF and call the SAME services/selectors as the API.
The server is the authority: a rejected board move returns an error and the
browser snaps the card back.
"""

import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from activity.selectors import task_activity
from core.exceptions import BusinessRuleError, VersionConflict
from core.web import VERSION_CONFLICT_MESSAGE, run_service, validation_messages
from projects.permissions import (
    Action,
    get_membership_or_404,
    permission_flags,
    require_permission,
)
from tasks import selectors, services
from tasks.forms import CommentForm, DependencyForm, TaskForm, TimeEntryForm
from tasks.models import Task
from tasks.workflow import STATUS_ORDER


@login_required
def board(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    columns = selectors.board(project_id)
    context = {
        "project": membership.project,
        "membership": membership,
        "can": permission_flags(membership),
        "columns": [
            (status, Task.Status(status).label, columns[status]) for status in STATUS_ORDER
        ],
        "active_tab": "board",
    }
    return render(request, "tasks/board.html", context)


@login_required
@require_POST
def board_move(request: HttpRequest, project_id: int, number: int) -> JsonResponse:
    """JSON endpoint for drag-and-drop: {"status", "position", "version"} -> new version."""
    membership = get_membership_or_404(request.user, project_id)
    try:
        payload = json.loads(request.body)
        version, position = int(payload["version"]), int(payload["position"])
        status = str(payload["status"])
    except (ValueError, KeyError, TypeError):
        return _json_error(400, "invalid", "Invalid move request.")
    try:
        task = services.move_task(
            membership=membership,
            number=number,
            expected_version=version,
            to_status=status,
            to_position=position,
        )
    except VersionConflict:
        return _json_error(409, "version_conflict", VERSION_CONFLICT_MESSAGE)
    except BusinessRuleError as error:
        return _json_error(400, error.code, error.message)
    except ValidationError as error:
        return _json_error(400, "invalid", " ".join(validation_messages(error)))
    except PermissionDenied as error:
        return _json_error(403, "permission_denied", str(error))
    return JsonResponse(
        {
            "number": task.number,
            "status": task.status,
            "position": task.position,
            "version": task.version,
        }
    )


@login_required
def task_create(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    require_permission(membership, Action.CREATE_TASK)
    form = TaskForm(request.POST or None, project_id=project_id)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        ok, task = run_service(
            request,
            lambda: services.create_task(membership=membership, **data),
            "Task created.",
        )
        if ok:
            return redirect("web-task-detail", project_id=project_id, number=task.number)
    context = {"project": membership.project, "form": form, "creating": True, "active_tab": "board"}
    return render(request, "tasks/task_form.html", context)


@login_required
def task_detail(request: HttpRequest, project_id: int, number: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    task = selectors.get_task_or_404(project_id, number)
    context = {
        "project": membership.project,
        "membership": membership,
        "can": permission_flags(membership),
        "task": task,
        "comments": selectors.task_comments(task.pk),
        "time_entries": selectors.task_time_entries(task.pk)[:20],
        "activity": task_activity(task)[:20],
        "comment_form": CommentForm(),
        "dependency_form": DependencyForm(),
        "time_form": TimeEntryForm(),
        "active_tab": "board",
    }
    return render(request, "tasks/task_detail.html", context)


@login_required
def task_edit(request: HttpRequest, project_id: int, number: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    task = selectors.get_task_or_404(project_id, number)
    require_permission(membership, Action.EDIT_TASK)
    initial = TaskForm.initial_from_task(task)
    form = TaskForm(request.POST or None, project_id=project_id, editing=True, initial=initial)
    if request.method == "POST" and form.is_valid():
        changes = dict(form.cleaned_data)
        version = changes.pop("version")
        ok, _ = run_service(
            request,
            lambda: services.update_task(
                membership=membership, number=number, expected_version=version, changes=changes
            ),
            "Task saved.",
        )
        if ok:
            return redirect("web-task-detail", project_id=project_id, number=number)
        # RACE: on a conflict (or rule error) reload so the form shows the latest version.
        return redirect("web-task-edit", project_id=project_id, number=number)
    context = {"project": membership.project, "form": form, "task": task, "active_tab": "board"}
    return render(request, "tasks/task_form.html", context)


@login_required
@require_POST
def task_delete(request: HttpRequest, project_id: int, number: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    services.delete_task(membership=membership, number=number)
    return redirect("web-board", project_id=project_id)


def _json_error(status: int, code: str, detail: str) -> JsonResponse:
    return JsonResponse({"code": code, "detail": detail}, status=status)
