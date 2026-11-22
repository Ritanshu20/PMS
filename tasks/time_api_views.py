"""
REST endpoints for time tracking and project reports.
Thin: validate, call time_services / report_selectors, serialize.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardPagination
from core.schema import ERROR_RESPONSE, paginated
from projects.permissions import get_membership_or_404
from tasks import report_selectors, selectors, time_services
from tasks.serializers import (
    DateRangeFilterSerializer,
    TimeByTaskFilterSerializer,
    TimeEntryCreateSerializer,
    TimeEntrySerializer,
    TimeEntryUpdateSerializer,
)


class TimeEntryListCreateView(APIView):
    @extend_schema(
        operation_id="time_entries_list", responses=paginated(TimeEntrySerializer, "TimeEntry")
    )
    def get(self, request: Request, project_id: int, number: int) -> Response:
        get_membership_or_404(request.user, project_id)
        task = selectors.get_task_or_404(project_id, number)
        paginator = StandardPagination()
        entries = selectors.task_time_entries(task.pk)
        page = paginator.paginate_queryset(entries, request, view=self)
        return paginator.get_paginated_response(TimeEntrySerializer(page, many=True).data)

    @extend_schema(
        request=TimeEntryCreateSerializer,
        responses={201: TimeEntrySerializer, 400: ERROR_RESPONSE},
    )
    def post(self, request: Request, project_id: int, number: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = TimeEntryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = time_services.log_time(
            membership=membership, task_number=number, **serializer.validated_data
        )
        return Response(TimeEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class TimeEntryDetailView(APIView):
    @extend_schema(request=TimeEntryUpdateSerializer, responses=TimeEntrySerializer)
    def patch(self, request: Request, project_id: int, entry_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = TimeEntryUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = time_services.update_time_entry(
            membership=membership, entry_id=entry_id, **serializer.validated_data
        )
        return Response(TimeEntrySerializer(entry).data)

    @extend_schema(responses={204: None, 403: ERROR_RESPONSE})
    def delete(self, request: Request, project_id: int, entry_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        time_services.delete_time_entry(membership=membership, entry_id=entry_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TimeByTaskReportView(APIView):
    @extend_schema(parameters=[TimeByTaskFilterSerializer], responses=OpenApiTypes.OBJECT)
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        filters = TimeByTaskFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        rows = report_selectors.time_by_task(project_id, filters.validated_data.get("sprint"))
        return Response({"results": rows})


class TimeByUserReportView(APIView):
    @extend_schema(parameters=[DateRangeFilterSerializer], responses=OpenApiTypes.OBJECT)
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        filters = DateRangeFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        rows = report_selectors.time_by_user(project_id, **filters.validated_data)
        return Response({"results": rows})


class TimeBySprintReportView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        return Response({"results": report_selectors.time_by_sprint(project_id)})


class WorkloadReportView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        return Response({"results": report_selectors.workload(project_id)})
