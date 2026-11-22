"""
Server-rendered project pages: my projects, create project, dashboard,
members & roles, activity feed. They call the same selectors/services as the API.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from activity.selectors import project_activity
from core.web import paginate, run_service
from projects import dashboard_selectors, selectors, services
from projects.forms import MemberAddForm, MemberRoleForm, ProjectForm
from projects.permissions import get_membership_or_404, permission_flags


@login_required
def project_list(request: HttpRequest) -> HttpResponse:
    projects = selectors.projects_for_user(request.user)
    return render(request, "projects/project_list.html", {"projects": projects})


@login_required
def project_create(request: HttpRequest) -> HttpResponse:
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ok, project = run_service(
            request,
            lambda: services.create_project(user=request.user, **form.cleaned_data),
            "Project created. You are its owner.",
        )
        if ok:
            return redirect("web-board", project_id=project.pk)
    return render(request, "projects/project_form.html", {"form": form})


@login_required
def dashboard(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    context = {
        "project": membership.project,
        "membership": membership,
        "data": dashboard_selectors.project_dashboard(project_id),
        "recent_activity": project_activity(project_id)[:10],
        "active_tab": "dashboard",
    }
    return render(request, "projects/dashboard.html", context)


@login_required
def activity_page(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    context = {
        "project": membership.project,
        "membership": membership,
        "page_obj": paginate(request, project_activity(project_id)),
        "active_tab": "activity",
    }
    return render(request, "projects/activity.html", context)


@login_required
def members_page(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    context = {
        "project": membership.project,
        "membership": membership,
        "can": permission_flags(membership),
        "members": selectors.project_members(project_id),
        "add_form": MemberAddForm(),
        "role_choices": MemberRoleForm.base_fields["role"].choices,
        "active_tab": "members",
    }
    return render(request, "projects/members.html", context)


@login_required
@require_POST
def member_add(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    form = MemberAddForm(request.POST)
    if form.is_valid():
        run_service(
            request,
            lambda: services.add_member(membership=membership, **form.cleaned_data),
            "Member added.",
        )
    else:
        _report_form_errors(request, form)
    return redirect("web-members", project_id=project_id)


@login_required
@require_POST
def member_change_role(request: HttpRequest, project_id: int, member_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    form = MemberRoleForm(request.POST)
    if form.is_valid():
        role = form.cleaned_data["role"]
        run_service(
            request,
            lambda: services.change_member_role(
                membership=membership, member_id=member_id, new_role=role
            ),
            "Role updated.",
        )
    return redirect("web-members", project_id=project_id)


@login_required
@require_POST
def member_remove(request: HttpRequest, project_id: int, member_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    ok, _ = run_service(
        request,
        lambda: services.remove_member(membership=membership, member_id=member_id),
        "Member removed.",
    )
    if ok and member_id == membership.pk:
        return redirect("web-project-list")
    return redirect("web-members", project_id=project_id)


@login_required
@require_POST
def transfer_ownership(request: HttpRequest, project_id: int, member_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    run_service(
        request,
        lambda: services.transfer_ownership(membership=membership, member_id=member_id),
        "Ownership transferred. You are now a manager.",
    )
    return redirect("web-members", project_id=project_id)


@login_required
@require_POST
def archive_project(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    run_service(
        request,
        lambda: services.archive_project(membership=membership),
        "Project archived. It is now read-only.",
    )
    return redirect("web-members", project_id=project_id)


def _report_form_errors(request: HttpRequest, form) -> None:
    for field_errors in form.errors.values():
        for message in field_errors:
            messages.error(request, message)
