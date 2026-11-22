from django.urls import path

from activity import api_views

urlpatterns = [
    path("", api_views.NotificationListView.as_view(), name="notification-list"),
    path(
        "unread-count/",
        api_views.NotificationUnreadCountView.as_view(),
        name="notification-unread-count",
    ),
    path("read-all/", api_views.NotificationReadAllView.as_view(), name="notification-read-all"),
    path(
        "<int:notification_id>/read/",
        api_views.NotificationReadView.as_view(),
        name="notification-read",
    ),
]
