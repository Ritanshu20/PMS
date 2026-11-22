"""
Server-rendered notifications page and its mark-read actions.
Only the current user's notifications are ever shown or changed.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from activity import selectors, services
from core.web import paginate


@login_required
def notifications_page(request: HttpRequest) -> HttpResponse:
    unread_only = request.GET.get("unread") == "1"
    notifications = selectors.notifications_for_user(request.user, unread_only=unread_only)
    context = {"page_obj": paginate(request, notifications), "unread_only": unread_only}
    return render(request, "activity/notifications.html", context)


@login_required
@require_POST
def notification_open(request: HttpRequest, notification_id: int) -> HttpResponse:
    """Mark as read, then go to the notification's link (always a local path)."""
    notification = services.mark_notification_read(
        user=request.user, notification_id=notification_id
    )
    # SAFETY: links are created by our services and always start with "/"; anything
    # else would be an open redirect, so fall back to the list.
    if notification.link.startswith("/") and not notification.link.startswith("//"):
        return redirect(notification.link)
    return redirect("web-notifications")


@login_required
@require_POST
def notifications_read_all(request: HttpRequest) -> HttpResponse:
    services.mark_all_notifications_read(user=request.user)
    return redirect("web-notifications")
