"""
Small helpers for the OpenAPI (Swagger) description. Documentation only:
nothing here changes how a request is handled.
"""

from drf_spectacular.utils import inline_serializer
from rest_framework import serializers


def paginated(item_serializer: type[serializers.Serializer], name: str) -> serializers.Serializer:
    """Describe a StandardPagination page: {count, next, previous, results: [...]}."""
    return inline_serializer(
        name=f"Paginated{name}",
        fields={
            "count": serializers.IntegerField(),
            "next": serializers.URLField(allow_null=True),
            "previous": serializers.URLField(allow_null=True),
            "results": item_serializer(many=True),
        },
    )


ERROR_RESPONSE = inline_serializer(
    name="Error",
    fields={
        "code": serializers.CharField(),
        "detail": serializers.JSONField(),
        "details": serializers.JSONField(required=False),
    },
)
