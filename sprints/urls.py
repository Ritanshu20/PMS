from django.urls import path

from sprints import api_views

urlpatterns = [
    path("sprints/", api_views.SprintListCreateView.as_view(), name="sprint-list"),
    path("sprints/<int:sprint_id>/", api_views.SprintDetailView.as_view(), name="sprint-detail"),
    path(
        "sprints/<int:sprint_id>/start/", api_views.SprintStartView.as_view(), name="sprint-start"
    ),
    path(
        "sprints/<int:sprint_id>/complete/",
        api_views.SprintCompleteView.as_view(),
        name="sprint-complete",
    ),
    path(
        "sprints/<int:sprint_id>/tasks/", api_views.SprintTasksView.as_view(), name="sprint-tasks"
    ),
    path(
        "sprints/<int:sprint_id>/tasks/<int:number>/",
        api_views.SprintTaskDetailView.as_view(),
        name="sprint-task-detail",
    ),
    path(
        "sprints/<int:sprint_id>/burndown/",
        api_views.SprintBurndownView.as_view(),
        name="sprint-burndown",
    ),
    path("velocity/", api_views.VelocityView.as_view(), name="velocity"),
    path("backlog/", api_views.BacklogView.as_view(), name="backlog"),
]
