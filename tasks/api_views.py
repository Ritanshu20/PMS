"""
REST endpoints for tasks, the board, labels, dependencies and "my tasks".
Comments live in `comment_api_views.py`, time tracking/reports in `time_api_views.py`.
Thin: validate input, call permissions/services/selectors, serialize output.
"""

from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardPagination
from core.schema import ERROR_RESPONSE, paginated
from projects.permissions import Action, get_membership_or_404, require_permission
from tasks import dependency_services, selectors, services
from tasks.serializers import (
    BoardSerializer,
    DependencyCreateSerializer,
    LabelSerializer,
    LabelUpdateSerializer,
    MyTasksFilterSerializer,
    TaskCreateSerializer,
    TaskFilterSerializer,
    TaskMoveSerializer,
    TaskSerializer,
    TaskUpdateSerializer,
)

TASK_UPDATE_EXAMPLE = OpenApiExample(
    "Edit with optimistic locking",
    description="Send the version you loaded. If someone saved first you get 409.",
    value={"version": 3, "title": "Fix login redirect", "status": "in_progress", "assignee_id": 7},
    request_only=True,
)
VERSION_CONFLICT_EXAMPLE = OpenApiExample(
    "409 version conflict",
    value={
        "code": "version_conflict",
        "detail": "This item was changed by someone else. Reload and try again.",
        "details": {"current": {"number": 12, "title": "Fix login", "version": 4}},
    },
    response_only=True,
    status_codes=["409"],
)
DEPENDENCY_EXAMPLE = OpenApiExample(
    "WEB-3 blocks this task",
    description="Rejected with code dependency_cycle if it would close a loop.",
    value={"blocker_number": 3},
    request_only=True,
)


def _task_response(project_id: int, number: int, http_status: int = status.HTTP_200_OK):
    task = selectors.get_task_or_404(project_id, number)
    return Response(TaskSerializer(task).data, status=http_status)


class TaskListCreateView(APIView):
    @extend_schema(
        operation_id="tasks_list",
        parameters=[TaskFilterSerializer],
        responses=paginated(TaskSerializer, "Task"),
    )
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        filters = TaskFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        tasks = selectors.list_tasks(project_id, filters.validated_data, user=request.user)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(tasks, request, view=self)
        return paginator.get_paginated_response(TaskSerializer(page, many=True).data)

    @extend_schema(request=TaskCreateSerializer, responses={201: TaskSerializer})
    def post(self, request: Request, project_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = TaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = services.create_task(membership=membership, **serializer.validated_data)
        return _task_response(project_id, task.number, status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    @extend_schema(responses={200: TaskSerializer, 404: ERROR_RESPONSE})
    def get(self, request: Request, project_id: int, number: int) -> Response:
        get_membership_or_404(request.user, project_id)
        return _task_response(project_id, number)

    @extend_schema(
        request=TaskUpdateSerializer,
        responses={200: TaskSerializer, 400: ERROR_RESPONSE, 409: ERROR_RESPONSE},
        examples=[TASK_UPDATE_EXAMPLE, VERSION_CONFLICT_EXAMPLE],
    )
    def patch(self, request: Request, project_id: int, number: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = TaskUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        version = changes.pop("version")
        services.update_task(
            membership=membership, number=number, expected_version=version, changes=changes
        )
        return _task_response(project_id, number)

    @extend_schema(responses={204: None, 403: ERROR_RESPONSE})
    def delete(self, request: Request, project_id: int, number: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        services.delete_task(membership=membership, number=number)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskMoveView(APIView):
    @extend_schema(
        request=TaskMoveSerializer,
        responses={200: TaskSerializer, 400: ERROR_RESPONSE, 409: ERROR_RESPONSE},
    )
    def post(self, request: Request, project_id: int, number: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = TaskMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.move_task(
            membership=membership,
            number=number,
            expected_version=serializer.validated_data["version"],
            to_status=serializer.validated_data["status"],
            to_position=serializer.validated_data["position"],
        )
        return _task_response(project_id, number)


class MyTasksView(APIView):
    """GET /api/my-tasks/?include_done=true: my assigned tasks across my projects."""

    @extend_schema(
        operation_id="my_tasks_list",
        parameters=[MyTasksFilterSerializer],
        responses=paginated(TaskSerializer, "MyTask"),
    )
    def get(self, request: Request) -> Response:
        filters = MyTasksFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        tasks = selectors.my_tasks(request.user, filters.validated_data["include_done"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(tasks, request, view=self)
        return paginator.get_paginated_response(TaskSerializer(page, many=True).data)


class BoardView(APIView):
    @extend_schema(responses=BoardSerializer)
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        columns = selectors.board(project_id)
        data = {column: TaskSerializer(tasks, many=True).data for column, tasks in columns.items()}
        return Response({"columns": data})


class LabelListCreateView(APIView):
    @extend_schema(operation_id="labels_list", responses=paginated(LabelSerializer, "Label"))
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(selectors.project_labels(project_id), request, view=self)
        return paginator.get_paginated_response(LabelSerializer(page, many=True).data)

    @extend_schema(request=LabelSerializer, responses={201: LabelSerializer})
    def post(self, request: Request, project_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        require_permission(membership, Action.MANAGE_LABELS)
        serializer = LabelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        label = services.create_label(membership=membership, **serializer.validated_data)
        return Response(LabelSerializer(label).data, status=status.HTTP_201_CREATED)


class LabelDetailView(APIView):
    @extend_schema(request=LabelUpdateSerializer, responses=LabelSerializer)
    def patch(self, request: Request, project_id: int, label_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = LabelUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        label = services.update_label(
            membership=membership, label_id=label_id, **serializer.validated_data
        )
        return Response(LabelSerializer(label).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, project_id: int, label_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        services.delete_label(membership=membership, label_id=label_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DependencyCreateView(APIView):
    """POST /tasks/<number>/dependencies/ {"blocker_number": N}: N blocks <number>."""

    @extend_schema(
        request=DependencyCreateSerializer,
        responses={201: TaskSerializer, 400: ERROR_RESPONSE},
        examples=[DEPENDENCY_EXAMPLE],
    )
    def post(self, request: Request, project_id: int, number: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = DependencyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dependency_services.add_dependency(
            membership=membership,
            blocker_number=serializer.validated_data["blocker_number"],
            blocked_number=number,
        )
        return _task_response(project_id, number, status.HTTP_201_CREATED)


class DependencyDeleteView(APIView):
    @extend_schema(responses={204: None, 404: ERROR_RESPONSE})
    def delete(self, request: Request, project_id: int, number: int, blocker_number: int):
        membership = get_membership_or_404(request.user, project_id)
        dependency_services.remove_dependency(
            membership=membership, blocker_number=blocker_number, blocked_number=number
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
