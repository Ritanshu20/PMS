"""
Framework-independent business errors and the DRF exception handler.

Services raise these errors; they never build HTTP responses. The API turns them
into JSON here, and template views turn them into messages.
Non-member access uses Django's `Http404`, missing permissions use Django's
`PermissionDenied`.

Every API error has the same shape:
    {"code": "<stable_machine_code>", "detail": <message or field errors>, "details": {...}?}
"""

from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class BusinessRuleError(Exception):
    """A request broke a business rule (400). `code` is a stable machine-readable string."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, code: str = "business_rule", details: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


class VersionConflict(BusinessRuleError):
    """Optimistic-locking failure (409): the row changed since the client read it."""

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, current_data: Any):
        super().__init__(
            "This item was changed by someone else. Reload and try again.",
            code="version_conflict",
            details={"current": current_data},
        )


def api_exception_handler(exc: Exception, context: dict) -> Response | None:
    """Turn every error into {"code", "detail"[, "details"]} with the right HTTP status."""
    if isinstance(exc, BusinessRuleError):
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "error_dict") else exc.messages
        return _error_response(status.HTTP_400_BAD_REQUEST, "invalid", detail)
    exc = _to_drf_exception(exc)
    response = exception_handler(exc, context)
    if response is None or not isinstance(exc, exceptions.APIException):
        return response
    if isinstance(exc, exceptions.ValidationError):
        response.data = {"code": "invalid", "detail": response.data}
    else:
        codes = exc.get_codes()
        code = codes if isinstance(codes, str) else exc.default_code
        response.data = {"code": code, "detail": response.data.get("detail", response.data)}
    return response


def _to_drf_exception(exc: Exception) -> Exception:
    """Keep the message of Django's Http404 / PermissionDenied (DRF would drop it)."""
    if isinstance(exc, Http404):
        return exceptions.NotFound(str(exc) or None)
    if isinstance(exc, DjangoPermissionDenied):
        return exceptions.PermissionDenied(str(exc) or None)
    return exc


def _error_response(http_status: int, code: str, detail: Any, details: Any = None) -> Response:
    body = {"code": code, "detail": detail}
    if details is not None:
        body["details"] = details
    return Response(body, status=http_status)
