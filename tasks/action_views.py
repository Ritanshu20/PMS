"""
POST-only actions on the task detail page: comments, dependencies and time logs.
Each action calls a service, shows a flash message and redirects back to the task.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from core.web import run_service
from projects.permissions import get_membership_or_404
from tasks import comment_services, dependency_services, time_services
from tasks.forms import CommentForm, DependencyForm, TimeEntryForm


def _back_to_task(project_id: int, number: int) -> HttpResponse:
    return redirect("web-task-detail", project_id=project_id, number=number)


def _form_error(request: HttpRequest, form) -> None:
    for field_errors in form.errors.values():
        for message in field_errors:
            messages.error(request, message)


@login_required
@require_POST
def comment_create(request: HttpRequest, project_id: int, number: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    form = CommentForm(request.POST)
    if form.is_valid():
        body = form.cleaned_data["body"]
        run_service(
            request,
            lambda: comment_services.create_comment(
                membership=membership, task_number=number, body=body
            ),
            "Comment added.",
        )
    else:
        _form_error(request, form)
    return _back_to_task(project_id, number)


@login_required
@require_POST
def comment_edit(request: HttpRequest, project_id: int, number: int, comment_id: int):
    membership = get_membership_or_404(request.user, project_id)
    form = CommentForm(request.POST)
    if form.is_valid():
        body = form.cleaned_data["body"]
        run_service(
            request,
            lambda: comment_services.edit_comment(
                membership=membership, task_number=number, comment_id=comment_id, body=body
            ),
            "Comment updated.",
        )
    else:
        _form_error(request, form)
    return _back_to_task(project_id, number)


@login_required
@require_POST
def comment_delete(request: HttpRequest, project_id: int, number: int, comment_id: int):
    membership = get_membership_or_404(request.user, project_id)
    run_service(
        request,
        lambda: comment_services.delete_comment(
            membership=membership, task_number=number, comment_id=comment_id
        ),
        "Comment deleted.",
    )
    return _back_to_task(project_id, number)


@login_required
@require_POST
def dependency_add(request: HttpRequest, project_id: int, number: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    form = DependencyForm(request.POST)
    if form.is_valid():
        blocker = form.cleaned_data["blocker_number"]
        run_service(
            request,
            lambda: dependency_services.add_dependency(
                membership=membership, blocker_number=blocker, blocked_number=number
            ),
            "Dependency added.",
        )
    else:
        _form_error(request, form)
    return _back_to_task(project_id, number)


@login_required
@require_POST
def dependency_remove(request: HttpRequest, project_id: int, number: int, blocker_number: int):
    membership = get_membership_or_404(request.user, project_id)
    run_service(
        request,
        lambda: dependency_services.remove_dependency(
            membership=membership, blocker_number=blocker_number, blocked_number=number
        ),
        "Dependency removed.",
    )
    return _back_to_task(project_id, number)


@login_required
@require_POST
def time_log(request: HttpRequest, project_id: int, number: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    form = TimeEntryForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        run_service(
            request,
            lambda: time_services.log_time(membership=membership, task_number=number, **data),
            "Time logged.",
        )
    else:
        _form_error(request, form)
    return _back_to_task(project_id, number)


@login_required
@require_POST
def time_delete(request: HttpRequest, project_id: int, number: int, entry_id: int):
    membership = get_membership_or_404(request.user, project_id)
    run_service(
        request,
        lambda: time_services.delete_time_entry(membership=membership, entry_id=entry_id),
        "Time entry deleted.",
    )
    return _back_to_task(project_id, number)
