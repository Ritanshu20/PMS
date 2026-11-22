from django.urls import path

from sprints import views

urlpatterns = [
    path("backlog/", views.backlog_page, name="web-backlog"),
    path("backlog/plan/", views.sprint_add_task, name="web-sprint-add-task"),
    path("sprints/new/", views.sprint_create, name="web-sprint-create"),
    path("sprints/<int:sprint_id>/", views.sprint_detail, name="web-sprint-detail"),
    path("sprints/<int:sprint_id>/start/", views.sprint_start, name="web-sprint-start"),
    path("sprints/<int:sprint_id>/complete/", views.sprint_complete, name="web-sprint-complete"),
    path(
        "sprints/<int:sprint_id>/tasks/<int:number>/remove/",
        views.sprint_remove_task,
        name="web-sprint-remove-task",
    ),
]
