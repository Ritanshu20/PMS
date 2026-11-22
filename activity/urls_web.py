from django.urls import path

from activity import views

urlpatterns = [
    path("", views.notifications_page, name="web-notifications"),
    path("read-all/", views.notifications_read_all, name="web-notifications-read-all"),
    path("<int:notification_id>/open/", views.notification_open, name="web-notification-open"),
]
