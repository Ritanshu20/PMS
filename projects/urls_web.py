from django.urls import path

from projects import views

urlpatterns = [
    path("", views.project_list, name="web-project-list"),
    path("new/", views.project_create, name="web-project-create"),
    path("<int:project_id>/dashboard/", views.dashboard, name="web-dashboard"),
    path("<int:project_id>/activity/", views.activity_page, name="web-activity"),
    path("<int:project_id>/members/", views.members_page, name="web-members"),
    path("<int:project_id>/members/add/", views.member_add, name="web-member-add"),
    path(
        "<int:project_id>/members/<int:member_id>/role/",
        views.member_change_role,
        name="web-member-role",
    ),
    path(
        "<int:project_id>/members/<int:member_id>/remove/",
        views.member_remove,
        name="web-member-remove",
    ),
    path(
        "<int:project_id>/members/<int:member_id>/transfer/",
        views.transfer_ownership,
        name="web-transfer-ownership",
    ),
    path("<int:project_id>/archive/", views.archive_project, name="web-project-archive"),
]
