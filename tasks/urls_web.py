from django.urls import path

from tasks import action_views, report_views, views

urlpatterns = [
    path("board/", views.board, name="web-board"),
    path("reports/time/", report_views.time_report, name="web-time-report"),
    path("tasks/new/", views.task_create, name="web-task-create"),
    path("tasks/<int:number>/", views.task_detail, name="web-task-detail"),
    path("tasks/<int:number>/edit/", views.task_edit, name="web-task-edit"),
    path("tasks/<int:number>/delete/", views.task_delete, name="web-task-delete"),
    path("tasks/<int:number>/move/", views.board_move, name="web-task-move"),
    path("tasks/<int:number>/comments/", action_views.comment_create, name="web-comment-create"),
    path(
        "tasks/<int:number>/comments/<int:comment_id>/edit/",
        action_views.comment_edit,
        name="web-comment-edit",
    ),
    path(
        "tasks/<int:number>/comments/<int:comment_id>/delete/",
        action_views.comment_delete,
        name="web-comment-delete",
    ),
    path(
        "tasks/<int:number>/dependencies/",
        action_views.dependency_add,
        name="web-dependency-add",
    ),
    path(
        "tasks/<int:number>/dependencies/<int:blocker_number>/delete/",
        action_views.dependency_remove,
        name="web-dependency-remove",
    ),
    path("tasks/<int:number>/time/", action_views.time_log, name="web-time-log"),
    path(
        "tasks/<int:number>/time/<int:entry_id>/delete/",
        action_views.time_delete,
        name="web-time-delete",
    ),
]
