from django.urls import path

from tasks import api_views, comment_api_views, time_api_views

urlpatterns = [
    path("tasks/", api_views.TaskListCreateView.as_view(), name="task-list"),
    path("tasks/<int:number>/", api_views.TaskDetailView.as_view(), name="task-detail"),
    path("tasks/<int:number>/move/", api_views.TaskMoveView.as_view(), name="task-move"),
    path(
        "tasks/<int:number>/dependencies/",
        api_views.DependencyCreateView.as_view(),
        name="task-dependencies",
    ),
    path(
        "tasks/<int:number>/dependencies/<int:blocker_number>/",
        api_views.DependencyDeleteView.as_view(),
        name="task-dependency-detail",
    ),
    path(
        "tasks/<int:number>/comments/",
        comment_api_views.CommentListCreateView.as_view(),
        name="task-comments",
    ),
    path(
        "tasks/<int:number>/comments/<int:comment_id>/",
        comment_api_views.CommentDetailView.as_view(),
        name="task-comment-detail",
    ),
    path(
        "tasks/<int:number>/time/",
        time_api_views.TimeEntryListCreateView.as_view(),
        name="task-time-entries",
    ),
    path("time/<int:entry_id>/", time_api_views.TimeEntryDetailView.as_view(), name="time-entry"),
    path(
        "reports/time-by-task/",
        time_api_views.TimeByTaskReportView.as_view(),
        name="report-time-by-task",
    ),
    path(
        "reports/time-by-user/",
        time_api_views.TimeByUserReportView.as_view(),
        name="report-time-by-user",
    ),
    path(
        "reports/time-by-sprint/",
        time_api_views.TimeBySprintReportView.as_view(),
        name="report-time-by-sprint",
    ),
    path("reports/workload/", time_api_views.WorkloadReportView.as_view(), name="report-workload"),
    path("board/", api_views.BoardView.as_view(), name="board"),
    path("labels/", api_views.LabelListCreateView.as_view(), name="label-list"),
    path("labels/<int:label_id>/", api_views.LabelDetailView.as_view(), name="label-detail"),
]
