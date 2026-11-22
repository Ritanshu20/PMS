"""
REST endpoints for task comments. Thin: validate, call comment_services, serialize.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardPagination
from core.schema import ERROR_RESPONSE, paginated
from projects.permissions import get_membership_or_404
from tasks import comment_services, selectors
from tasks.serializers import CommentSerializer, CommentWriteSerializer


class CommentListCreateView(APIView):
    @extend_schema(operation_id="comments_list", responses=paginated(CommentSerializer, "Comment"))
    def get(self, request: Request, project_id: int, number: int) -> Response:
        get_membership_or_404(request.user, project_id)
        task = selectors.get_task_or_404(project_id, number)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(selectors.task_comments(task.pk), request, view=self)
        return paginator.get_paginated_response(CommentSerializer(page, many=True).data)

    @extend_schema(request=CommentWriteSerializer, responses={201: CommentSerializer})
    def post(self, request: Request, project_id: int, number: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = CommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = comment_services.create_comment(
            membership=membership, task_number=number, body=serializer.validated_data["body"]
        )
        return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class CommentDetailView(APIView):
    @extend_schema(
        request=CommentWriteSerializer, responses={200: CommentSerializer, 403: ERROR_RESPONSE}
    )
    def patch(self, request: Request, project_id: int, number: int, comment_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        serializer = CommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = comment_services.edit_comment(
            membership=membership,
            task_number=number,
            comment_id=comment_id,
            body=serializer.validated_data["body"],
        )
        return Response(CommentSerializer(comment).data)

    @extend_schema(responses={204: None, 403: ERROR_RESPONSE})
    def delete(self, request: Request, project_id: int, number: int, comment_id: int) -> Response:
        membership = get_membership_or_404(request.user, project_id)
        comment_services.delete_comment(
            membership=membership, task_number=number, comment_id=comment_id
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
