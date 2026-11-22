from django.http import HttpRequest

from activity.selectors import unread_notification_count


def unread_notifications(request: HttpRequest) -> dict[str, int]:
    """Unread notification count for the navbar badge (one COUNT query per page)."""
    if not request.user.is_authenticated:
        return {}
    return {"unread_notifications": unread_notification_count(request.user)}
