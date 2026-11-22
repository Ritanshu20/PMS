from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness + database check. Public, never throttled, returns no sensitive data."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes: list = []

    @extend_schema(
        responses=inline_serializer(
            "Health", {"status": serializers.CharField(), "database": serializers.CharField()}
        )
    )
    def get(self, request: Request) -> Response:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return Response({"status": "ok", "database": "ok"})
