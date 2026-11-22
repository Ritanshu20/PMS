"""
REST endpoints for projects, members and the project dashboard. Thin: parse
input, call permissions/services/selectors, serialize output.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardPagination
from core.schema import ERROR_RESPONSE, paginated
from projects import dashboard_selectors, selectors, services
from projects.permissions import get_membership_or_404
from projects.serializers import (
    MemberAddSerializer,
    MemberFilterSerializer,
    MemberRoleSerializer,
    MemberSerializer,
    ProjectCreateSerializer,
    ProjectSerializer,
    ProjectUpdateSerializer,
    TransferOwnershipSerializer,
)


class ProjectListCreateView(ListAPIView):
    """GET: my projects (paginated). POST: create a project; I become its owner."""

    serializer_class = ProjectSerializer

    def get_queryset(self):
        return selectors.projects_for_user(self.request.user)

    @extend_schema(
        request=ProjectCreateSerializer, responses={201: ProjectSerializer, 400: ERROR_RESPONSE}
    )
    def post(self, request: Request) -> Response:
        serializer = ProjectCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = services.create_project(user=request.user, **serializer.validated_data)
        project = selectors.projects_for_user(request.user).get(pk=project.pk)
        return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)


class ProjectDetailView(APIView):
    @extend_schema(responses={200: ProjectSerializer, 404: ERROR_RESPONSE})
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        project = selectors.projects_for_user(request.user).get(pk=project_id)
        return Response(ProjectSerializer(project).data)

    @extend_schema(request=ProjectUpdateSerializer, responses=ProjectSerializer)
    def patch(self, request: Request, project_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = ProjectUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_project(membership=membership, **serializer.validated_data)
        project = selectors.projects_for_user(request.user).get(pk=project_id)
        return Response(ProjectSerializer(project).data)

    @extend_schema(responses={204: None, 403: ERROR_RESPONSE})
    def delete(self, request: Request, project_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        services.delete_project(membership=membership)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectDashboardView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        return Response(dashboard_selectors.project_dashboard(project_id))


class ProjectArchiveView(APIView):
    @extend_schema(request=None, responses=ProjectSerializer)
    def post(self, request: Request, project_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        services.archive_project(membership=membership)
        project = selectors.projects_for_user(request.user).get(pk=project_id)
        return Response(ProjectSerializer(project).data)


class TransferOwnershipView(APIView):
    @extend_schema(request=TransferOwnershipSerializer, responses=MemberSerializer)
    def post(self, request: Request, project_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = TransferOwnershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = services.transfer_ownership(
            membership=membership, member_id=serializer.validated_data["member_id"]
        )
        return Response(MemberSerializer(target).data)


class MemberListCreateView(APIView):
    @extend_schema(
        operation_id="projects_members_list",
        parameters=[MemberFilterSerializer],
        responses=paginated(MemberSerializer, "Member"),
    )
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        filters = MemberFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        members = selectors.project_members(project_id, filters.validated_data.get("role"))
        paginator = StandardPagination()
        page = paginator.paginate_queryset(members, request, view=self)
        return paginator.get_paginated_response(MemberSerializer(page, many=True).data)

    @extend_schema(request=MemberAddSerializer, responses={201: MemberSerializer})
    def post(self, request: Request, project_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = MemberAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.add_member(membership=membership, **serializer.validated_data)
        return Response(MemberSerializer(member).data, status=status.HTTP_201_CREATED)


class MemberDetailView(APIView):
    @extend_schema(request=MemberRoleSerializer, responses=MemberSerializer)
    def patch(self, request: Request, project_id: int, member_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = MemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.change_member_role(
            membership=membership, member_id=member_id, new_role=serializer.validated_data["role"]
        )
        return Response(MemberSerializer(member).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, project_id: int, member_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        services.remove_member(membership=membership, member_id=member_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
