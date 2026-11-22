"""
Server-rendered insight pages: time report for a project and "my tasks".
They only read, through `tasks.report_selectors` / `tasks.selectors`.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from core.web import paginate
from projects.permissions import get_membership_or_404
from tasks import report_selectors, selectors
from tasks.serializers import DateRangeFilterSerializer, MyTasksFilterSerializer


@login_required
def time_report(request: HttpRequest, project_id: int) -> HttpResponse:
    membership = get_membership_or_404(request.user, project_id)
    dates = DateRangeFilterSerializer(data=request.GET)
    date_range = dates.validated_data if dates.is_valid() else {}
    context = {
        "project": membership.project,
        "membership": membership,
        "by_task": report_selectors.time_by_task(project_id),
        "by_user": report_selectors.time_by_user(project_id, **date_range),
        "by_sprint": report_selectors.time_by_sprint(project_id),
        "workload": report_selectors.workload(project_id),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "active_tab": "time",
    }
    return render(request, "tasks/time_report.html", context)


@login_required
def my_tasks(request: HttpRequest) -> HttpResponse:
    filters = MyTasksFilterSerializer(data=request.GET)
    include_done = filters.validated_data["include_done"] if filters.is_valid() else False
    context = {
        "page_obj": paginate(
            request, selectors.my_tasks(request.user, include_done).prefetch_related(None)
        ),
        "include_done": include_done,
    }
    return render(request, "tasks/my_tasks.html", context)
