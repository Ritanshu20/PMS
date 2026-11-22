from django.urls import path

from projects import api_views

urlpatterns = [
    path("", api_views.ProjectListCreateView.as_view(), name="project-list"),
    path("<int:project_id>/", api_views.ProjectDetailView.as_view(), name="project-detail"),
    path(
        "<int:project_id>/archive/", api_views.ProjectArchiveView.as_view(), name="project-archive"
    ),
    path(
        "<int:project_id>/dashboard/",
        api_views.ProjectDashboardView.as_view(),
        name="project-dashboard",
    ),
    path(
        "<int:project_id>/transfer-ownership/",
        api_views.TransferOwnershipView.as_view(),
        name="project-transfer-ownership",
    ),
    path(
        "<int:project_id>/members/",
        api_views.MemberListCreateView.as_view(),
        name="project-members",
    ),
    path(
        "<int:project_id>/members/<int:member_id>/",
        api_views.MemberDetailView.as_view(),
        name="project-member-detail",
    ),
]
