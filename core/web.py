"""
Helpers shared by the server-rendered (template) views.

Template views call the SAME services as the API. This module turns business
errors into Django flash messages so pages never re-implement rules.
Http404 and PermissionDenied are NOT caught: Django renders the 404/403 pages.
"""

from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.http import HttpRequest

from core.exceptions import BusinessRuleError, VersionConflict

PAGE_SIZE = 20

VERSION_CONFLICT_MESSAGE = (
    "Someone else changed this task while you were editing. "
    "The page now shows the latest version; please apply your change again."
)


def run_service(
    request: HttpRequest, action: Callable[[], Any], success_message: str = ""
) -> tuple[bool, Any]:
    """Run a service call and report the outcome as a flash message.

    Returns:
        (True, result) on success, (False, None) when a business rule or
        validation error was shown to the user.

    Example:
        ok, task = run_service(request, lambda: services.create_task(...), "Task created.")
    """
    try:
        result = action()
    except VersionConflict:
        messages.error(request, VERSION_CONFLICT_MESSAGE)
        return False, None
    except BusinessRuleError as error:
        messages.error(request, error.message)
        return False, None
    except ValidationError as error:
        messages.error(request, " ".join(validation_messages(error)))
        return False, None
    if success_message:
        messages.success(request, success_message)
    return True, result


def paginate(request: HttpRequest, items, per_page: int = PAGE_SIZE) -> Page:
    """Page of `items` for ?page=N (bad or too-large numbers fall back safely)."""
    return Paginator(items, per_page).get_page(request.GET.get("page"))


def validation_messages(error: ValidationError) -> list[str]:
    """Flatten a Django ValidationError into readable sentences."""
    if hasattr(error, "error_dict"):
        return [
            f"{field.replace('_', ' ').capitalize()}: {message}"
            for field, field_messages in error.message_dict.items()
            for message in field_messages
        ]
    return list(error.messages)
