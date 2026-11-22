from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from activity.models import ActivityLog
from core.testing import ProjectWithRolesMixin, add_member, make_project, make_user
from projects.models import Project, ProjectMember
from tasks.models import Task

Role = ProjectMember.Role


class ProjectCreateAndListTests(ProjectWithRolesMixin, TestCase):
    def test_creator_becomes_owner_and_key_is_uppercased(self):
        """POST /api/projects/ makes the creator the owner; "app" is stored as "APP"."""
        response = self.client_for(self.outsider).post(
            "/api/projects/", {"name": "Mobile", "key": "app"}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["key"], "APP")
        self.assertEqual(response.json()["my_role"], "owner")
        project = Project.objects.get(key="APP")
        self.assertTrue(
            ProjectMember.objects.filter(project=project, user=self.outsider, role="owner").exists()
        )
        self.assertTrue(
            ActivityLog.objects.filter(project=project, verb="project.created").exists()
        )

    def test_invalid_keys_are_rejected(self):
        """Keys must be 2-6 letters A-Z."""
        client = self.client_for(self.outsider)
        for bad_key in ["W", "TOOLONGX", "W3B", "", "WE B"]:
            with self.subTest(key=bad_key):
                response = client.post("/api/projects/", {"name": "X", "key": bad_key})
                self.assertEqual(response.status_code, 400)

    def test_duplicate_key_is_rejected(self):
        """Keys are globally unique."""
        response = self.client_for(self.outsider).post(
            "/api/projects/", {"name": "Other", "key": "web"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("key", response.json()["detail"])

    def test_list_shows_only_my_projects_with_my_role(self):
        """There is no global project list."""
        make_project(self.outsider, key="SECRET", name="Hidden")
        response = self.client_for(self.viewer).get("/api/projects/")
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual([p["key"] for p in results], ["WEB"])
        self.assertEqual(results[0]["my_role"], "viewer")
        self.assertEqual(results[0]["member_count"], 4)

    def test_project_list_query_count_does_not_grow_with_projects(self):
        """No N+1: 1 project or 6 projects cost the same number of queries."""
        client = self.client_for(self.owner)
        with CaptureQueriesContext(connection) as one_project:
            client.get("/api/projects/")
        for index in range(5):
            make_project(self.owner, key=f"P{chr(65 + index)}", name=f"P{index}")
        with CaptureQueriesContext(connection) as six_projects:
            response = client.get("/api/projects/")
        self.assertEqual(len(response.json()["results"]), 6)
        self.assertEqual(len(one_project), len(six_projects))


class NonMemberAccessTests(ProjectWithRolesMixin, TestCase):
    def test_outsider_gets_404_on_every_project_endpoint(self):
        """Non-members get 404 (never 403), exactly like a missing project."""
        client = self.client_for(self.outsider)
        base = f"/api/projects/{self.project.pk}"
        member_id = ProjectMember.objects.get(user=self.member).pk
        calls = [
            ("get", f"{base}/", None),
            ("patch", f"{base}/", {"name": "x"}),
            ("delete", f"{base}/", None),
            ("post", f"{base}/archive/", None),
            ("post", f"{base}/transfer-ownership/", {"member_id": member_id}),
            ("get", f"{base}/members/", None),
            ("post", f"{base}/members/", {"email": "a@example.com", "role": "member"}),
            ("patch", f"{base}/members/{member_id}/", {"role": "viewer"}),
            ("delete", f"{base}/members/{member_id}/", None),
            ("get", "/api/projects/999999/", None),
        ]
        for method, url, data in calls:
            with self.subTest(method=method, url=url):
                response = getattr(client, method)(url, data)
                self.assertEqual(response.status_code, 404)

    def test_anonymous_user_gets_401(self):
        """The API requires authentication before anything else."""
        response = self.client_for(None).get(f"/api/projects/{self.project.pk}/")
        self.assertEqual(response.status_code, 401)


class ProjectUpdateArchiveDeleteTests(ProjectWithRolesMixin, TestCase):
    def url(self, suffix: str = "") -> str:
        return f"/api/projects/{self.project.pk}/{suffix}"

    def test_edit_project_allowed_for_manager_and_owner_only(self):
        """Matrix: project settings are managed by managers and owners."""
        expected = {"viewer": 403, "member": 403, "manager": 200, "owner": 200}
        for role, code in expected.items():
            with self.subTest(role=role):
                response = self.client_for(self.users_by_role[role]).patch(
                    self.url(), {"name": f"Renamed by {role}"}
                )
                self.assertEqual(response.status_code, code)

    def test_only_owner_can_archive(self):
        """Matrix: archive is owner-only."""
        for role in ("viewer", "member", "manager"):
            with self.subTest(role=role):
                response = self.client_for(self.users_by_role[role]).post(self.url("archive/"))
                self.assertEqual(response.status_code, 403)
        response = self.client_for(self.owner).post(self.url("archive/"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "archived")

    def test_archived_project_is_read_only(self):
        """After archiving, reads work but every write gets 403."""
        self.client_for(self.owner).post(self.url("archive/"))
        client = self.client_for(self.owner)
        self.assertEqual(client.get(self.url()).status_code, 200)
        self.assertEqual(client.patch(self.url(), {"name": "x"}).status_code, 403)
        add = client.post(self.url("members/"), {"email": self.outsider.email, "role": "member"})
        self.assertEqual(add.status_code, 403)
        task = client.post(self.url("tasks/"), {"title": "New"})
        self.assertEqual(task.status_code, 403)

    def test_only_owner_can_delete_project(self):
        """Matrix: delete is owner-only and removes the project."""
        self.assertEqual(self.client_for(self.manager).delete(self.url()).status_code, 403)
        self.assertEqual(self.client_for(self.owner).delete(self.url()).status_code, 204)
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())


class MemberManagementTests(ProjectWithRolesMixin, TestCase):
    def members_url(self, member_id: int | None = None) -> str:
        base = f"/api/projects/{self.project.pk}/members/"
        return f"{base}{member_id}/" if member_id else base

    def member_id(self, user) -> int:
        return ProjectMember.objects.get(project=self.project, user=user).pk

    def test_manager_adds_member_by_exact_email(self):
        """Members are added by email; the response shows only public user fields."""
        response = self.client_for(self.manager).post(
            self.members_url(), {"email": self.outsider.email, "role": "member"}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["user"]["username"], "oscar")
        self.assertNotIn("email", response.json()["user"])

    def test_member_and_viewer_cannot_manage_members(self):
        """Matrix: member management is for managers and owners."""
        for role in ("member", "viewer"):
            with self.subTest(role=role):
                response = self.client_for(self.users_by_role[role]).post(
                    self.members_url(), {"email": self.outsider.email, "role": "viewer"}
                )
                self.assertEqual(response.status_code, 403)

    def test_manager_cannot_add_or_change_owners(self):
        """Managers cannot create owners or modify existing owners."""
        client = self.client_for(self.manager)
        add_owner = client.post(self.members_url(), {"email": self.outsider.email, "role": "owner"})
        self.assertEqual(add_owner.status_code, 403)
        demote = client.patch(self.members_url(self.member_id(self.owner)), {"role": "member"})
        self.assertEqual(demote.status_code, 403)
        remove = client.delete(self.members_url(self.member_id(self.owner)))
        self.assertEqual(remove.status_code, 403)

    def test_unknown_email_and_existing_member_are_rejected(self):
        """Clear 400s instead of silent failures."""
        client = self.client_for(self.owner)
        unknown = client.post(self.members_url(), {"email": "ghost@example.com", "role": "member"})
        self.assertEqual(unknown.status_code, 400)
        again = client.post(self.members_url(), {"email": self.member.email, "role": "member"})
        self.assertEqual(again.status_code, 400)

    def test_last_owner_cannot_be_demoted_or_removed(self):
        """A project always keeps at least one owner."""
        client = self.client_for(self.owner)
        demote = client.patch(self.members_url(self.member_id(self.owner)), {"role": "manager"})
        self.assertEqual(demote.status_code, 400)
        self.assertEqual(demote.json()["code"], "last_owner")
        remove = client.delete(self.members_url(self.member_id(self.owner)))
        self.assertEqual(remove.status_code, 400)

    def test_owner_can_step_down_when_another_owner_exists(self):
        """With two owners, one may demote themself."""
        second_owner = make_user("second")
        add_member(self.project, second_owner, Role.OWNER)
        response = self.client_for(self.owner).patch(
            self.members_url(self.member_id(self.owner)), {"role": "manager"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "manager")

    def test_removing_member_unassigns_their_tasks(self):
        """Assignees must be members, so removal clears their assignments."""
        task = Task.objects.create(
            project=self.project, number=1, title="t", reporter=self.owner, assignee=self.member
        )
        response = self.client_for(self.owner).delete(self.members_url(self.member_id(self.member)))
        self.assertEqual(response.status_code, 204)
        task.refresh_from_db()
        self.assertIsNone(task.assignee_id)
        self.assertEqual(task.version, 2)

    def test_demoting_to_viewer_unassigns_tasks(self):
        """Viewers cannot be assignees."""
        task = Task.objects.create(
            project=self.project, number=1, title="t", reporter=self.owner, assignee=self.member
        )
        self.client_for(self.owner).patch(
            self.members_url(self.member_id(self.member)), {"role": "viewer"}
        )
        task.refresh_from_db()
        self.assertIsNone(task.assignee_id)

    def test_member_id_from_another_project_gives_404(self):
        """IDOR: a member id is only valid inside its own project."""
        other_project = make_project(self.outsider, key="OTH")
        other_member = add_member(other_project, make_user("zed"), Role.MEMBER)
        response = self.client_for(self.owner).patch(
            self.members_url(other_member.pk), {"role": "viewer"}
        )
        self.assertEqual(response.status_code, 404)

    def test_transfer_ownership_swaps_roles(self):
        """New owner gets owner; the previous owner becomes manager."""
        response = self.client_for(self.owner).post(
            f"/api/projects/{self.project.pk}/transfer-ownership/",
            {"member_id": self.member_id(self.member)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProjectMember.objects.get(user=self.member).role, Role.OWNER)
        self.assertEqual(ProjectMember.objects.get(user=self.owner).role, Role.MANAGER)

    def test_only_owner_can_transfer_ownership(self):
        """Matrix: transfer is owner-only."""
        response = self.client_for(self.manager).post(
            f"/api/projects/{self.project.pk}/transfer-ownership/",
            {"member_id": self.member_id(self.member)},
        )
        self.assertEqual(response.status_code, 403)

    def test_every_member_role_change_is_logged(self):
        """Every state change writes an ActivityLog row."""
        self.client_for(self.owner).patch(
            self.members_url(self.member_id(self.member)), {"role": "manager"}
        )
        log = ActivityLog.objects.get(verb="member.role_changed")
        self.assertEqual(log.changes["role"], ["member", "manager"])
