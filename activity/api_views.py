"""
REST endpoints for the current user's notifications and a project's activity feed.
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from activity import selectors, services
from activity.serializers import (
    ActivitySerializer,
    NotificationFilterSerializer,
    NotificationSerializer,
)
from core.pagination import StandardPagination
from core.schema import paginated
from projects.permissions import get_membership_or_404


class NotificationListView(APIView):
    @extend_schema(
        operation_id="notifications_list",
        parameters=[NotificationFilterSerializer],
        responses=paginated(NotificationSerializer, "Notification"),
    )
    def get(self, request: Request) -> Response:
        filters = NotificationFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        notifications = selectors.notifications_for_user(
            request.user, unread_only=filters.validated_data["unread"]
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(notifications, request, view=self)
        return paginator.get_paginated_response(NotificationSerializer(page, many=True).data)


class NotificationUnreadCountView(APIView):
    @extend_schema(
        responses=inline_serializer("UnreadCount", {"unread": serializers.IntegerField()})
    )
    def get(self, request: Request) -> Response:
        return Response({"unread": selectors.unread_notification_count(request.user)})


class NotificationReadView(APIView):
    @extend_schema(request=None, responses=NotificationSerializer)
    def post(self, request: Request, notification_id: int) -> Response:
        notification = services.mark_notification_read(
            user=request.user, notification_id=notification_id
        )
        return Response(NotificationSerializer(notification).data)


class NotificationReadAllView(APIView):
    @extend_schema(
        request=None,
        responses=inline_serializer("MarkedRead", {"marked_read": serializers.IntegerField()}),
    )
    def post(self, request: Request) -> Response:
        updated = services.mark_all_notifications_read(user=request.user)
        return Response({"marked_read": updated})


class ProjectActivityView(APIView):
    @extend_schema(
        operation_id="project_activity_list", responses=paginated(ActivitySerializer, "Activity")
    )
    def get(self, request: Request, project_id: int) -> Response:
        get_membership_or_404(request.user, project_id)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(
            selectors.project_activity(project_id), request, view=self
        )
        return paginator.get_paginated_response(ActivitySerializer(page, many=True).data)
