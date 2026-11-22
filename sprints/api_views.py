"""
REST endpoints for sprints, burndown, velocity and the backlog.
Thin: validate, call services/selectors, serialize.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardPagination
from core.schema import ERROR_RESPONSE, paginated
from projects.permissions import get_membership_or_404
from sprints import selectors, services
from sprints.serializers import (
    SprintCompleteSerializer,
    SprintCreateSerializer,
    SprintFilterSerializer,
    SprintSerializer,
    SprintTaskSerializer,
    SprintUpdateSerializer,
)
from tasks import selectors as task_selectors
from tasks.serializers import TaskSerializer

SPRINT_START_CONFLICT_EXAMPLE = OpenApiExample(
    "Another sprint is already active",
    value={
        "code": "sprint_already_active",
        "detail": "Another sprint is already active. Complete it first.",
    },
    response_only=True,
    status_codes=["400"],
)


def _sprint_response(project_id: int, sprint_id: int, http_status: int = status.HTTP_200_OK):
    sprint = selectors.get_sprint_or_404(project_id, sprint_id)
    return Response(SprintSerializer(sprint).data, status=http_status)


class SprintListCreateView(APIView):
    @extend_schema(
        operation_id="sprints_list",
        parameters=[SprintFilterSerializer],
        responses=paginated(SprintSerializer, "Sprint"),
    )
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        filters = SprintFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        sprints = selectors.sprints_for_project(project_id, filters.validated_data.get("status"))
        paginator = StandardPagination()
        page = paginator.paginate_queryset(sprints, request, view=self)
        return paginator.get_paginated_response(SprintSerializer(page, many=True).data)

    @extend_schema(request=SprintCreateSerializer, responses={201: SprintSerializer})
    def post(self, request: Request, project_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = SprintCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sprint = services.create_sprint(membership=membership, **serializer.validated_data)
        return _sprint_response(project_id, sprint.pk, status.HTTP_201_CREATED)


class SprintDetailView(APIView):
    @extend_schema(responses=SprintSerializer)
    def get(self, request: Request, project_id: int, sprint_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        return _sprint_response(project_id, sprint_id)

    @extend_schema(request=SprintUpdateSerializer, responses=SprintSerializer)
    def patch(self, request: Request, project_id: int, sprint_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = SprintUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_sprint(
            membership=membership, sprint_id=sprint_id, **serializer.validated_data
        )
        return _sprint_response(project_id, sprint_id)

    @extend_schema(responses={204: None, 400: ERROR_RESPONSE})
    def delete(self, request: Request, project_id: int, sprint_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        services.delete_sprint(membership=membership, sprint_id=sprint_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SprintStartView(APIView):
    @extend_schema(
        request=None,
        responses={200: SprintSerializer, 400: ERROR_RESPONSE},
        examples=[SPRINT_START_CONFLICT_EXAMPLE],
        description="Only one sprint per project can be active. Sets committed_points.",
    )
    def post(self, request: Request, project_id: int, sprint_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        services.start_sprint(membership=membership, sprint_id=sprint_id)
        return _sprint_response(project_id, sprint_id)


class SprintCompleteView(APIView):
    @extend_schema(request=SprintCompleteSerializer, responses=SprintSerializer)
    def post(self, request: Request, project_id: int, sprint_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = SprintCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.complete_sprint(
            membership=membership, sprint_id=sprint_id, **serializer.validated_data
        )
        return _sprint_response(project_id, sprint_id)


class SprintTasksView(APIView):
    """GET lists the sprint's tasks; POST {"task_number": N} plans a task into it."""

    @extend_schema(
        operation_id="sprint_tasks_list", responses=paginated(TaskSerializer, "SprintTask")
    )
    def get(self, request: Request, project_id: int, sprint_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        selectors.get_sprint_or_404(project_id, sprint_id)
        tasks = task_selectors.list_tasks(project_id, {"sprint": str(sprint_id)})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(tasks, request, view=self)
        return paginator.get_paginated_response(TaskSerializer(page, many=True).data)

    @extend_schema(request=SprintTaskSerializer, responses={201: TaskSerializer})
    def post(self, request: Request, project_id: int, sprint_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = SprintTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = services.add_task_to_sprint(
            membership=membership,
            sprint_id=sprint_id,
            task_number=serializer.validated_data["task_number"],
        )
        data = TaskSerializer(task_selectors.get_task_or_404(project_id, task.number)).data
        return Response(data, status=status.HTTP_201_CREATED)


class SprintTaskDetailView(APIView):
    @extend_schema(responses={204: None})
    def delete(self, request: Request, project_id: int, sprint_id: int, number: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        services.remove_task_from_sprint(
            membership=membership, sprint_id=sprint_id, task_number=number
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SprintBurndownView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request: Request, project_id: int, sprint_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        sprint = selectors.get_sprint_or_404(project_id, sprint_id)
        return Response(selectors.burndown(sprint))


class VelocityView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        return Response(selectors.velocity(project_id))


class BacklogView(APIView):
    @extend_schema(operation_id="backlog_list", responses=paginated(TaskSerializer, "BacklogTask"))
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        tasks = task_selectors.backlog(project_id)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(tasks, request, view=self)
        return paginator.get_paginated_response(TaskSerializer(page, many=True).data)
