"""
Read queries for projects and members.

Every query is scoped to the requesting user via `permissions`; never return a
project the user is not a member of.
"""

from django.db.models import Count, OuterRef, QuerySet, Subquery

from projects.models import Project, ProjectMember
from projects.permissions import visible_projects_filter


def projects_for_user(user) -> QuerySet[Project]:
    """The user's projects with `my_role` and `member_count` annotated (one query).

    Why: there is no global project list; users only ever see their own projects.
    """
    my_role = ProjectMember.objects.filter(project=OuterRef("pk"), user=user).values("role")[:1]
    return (
        Project.objects.filter(visible_projects_filter(user))
        .annotate(my_role=Subquery(my_role), member_count=Count("members", distinct=True))
        .order_by("name", "id")
    )


def project_members(project_id: int, role: str | None = None) -> QuerySet[ProjectMember]:
    """Members of one project (optionally one role) with their users loaded."""
    queryset = ProjectMember.objects.filter(project_id=project_id)
    if role:
        queryset = queryset.filter(role=role)
    return queryset.select_related("user").order_by("user__username")
