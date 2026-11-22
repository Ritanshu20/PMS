from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from activity.api_views import ProjectActivityView
from tasks.api_views import MyTasksView
from tasks.report_views import my_tasks

api_patterns = [
    path("api/", include("core.urls")),
    path("api/auth/", include("accounts.urls")),
    path("api/notifications/", include("activity.urls")),
    path("api/my-tasks/", MyTasksView.as_view(), name="my-tasks"),
    path("api/projects/", include("projects.urls")),
    path("api/projects/<int:project_id>/", include("tasks.urls")),
    path("api/projects/<int:project_id>/", include("sprints.urls")),
    path(
        "api/projects/<int:project_id>/activity/",
        ProjectActivityView.as_view(),
        name="project-activity",
    ),
]

# Server-rendered pages (session auth + CSRF). They share services with the API.
web_patterns = [
    path("", RedirectView.as_view(pattern_name="web-project-list"), name="home"),
    path("accounts/", include("accounts.urls_web")),
    path("projects/", include("projects.urls_web")),
    path("projects/<int:project_id>/", include("tasks.urls_web")),
    path("projects/<int:project_id>/", include("sprints.urls_web")),
    path("my-tasks/", my_tasks, name="web-my-tasks"),
    path("notifications/", include("activity.urls_web")),
]

urlpatterns = [path("admin/", admin.site.urls), *api_patterns, *web_patterns]
