from django.test import TestCase

from activity.models import Notification
from core.testing import ProjectWithRolesMixin, make_user
from projects.models import Project
from tasks.mentions import extract_usernames
from tasks.models import Comment, Task


class MentionParsingTests(TestCase):
    def test_extracts_usernames_and_ignores_email_addresses(self):
        """@names are found; the @ inside an email is not a mention."""
        found = extract_usernames("Hi @Asha and @ravi.k. Mail me at a@example.com")
        self.assertIn("asha", found)
        self.assertIn("ravi.k", found)
        self.assertNotIn("example.com", found)


class CommentTestCase(ProjectWithRolesMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = Task.objects.create(
            project=self.project, number=1, title="Login page", reporter=self.owner
        )
        self.url = f"/api/projects/{self.project.pk}/tasks/1/comments/"

    def comment(self, body: str, user=None):
        return self.client_for(user or self.member).post(self.url, {"body": body})

    def mentioned_usernames(self) -> list[str]:
        return sorted(
            Notification.objects.filter(type="mentioned").values_list("user__username", flat=True)
        )


class MentionNotificationTests(CommentTestCase):
    def test_members_mentioned_get_one_notification_each(self):
        """@manoj @vikram -> both members notified once, even if named twice."""
        response = self.comment("@manoj and @vikram please check. Thanks @manoj!")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.mentioned_usernames(), ["manoj", "vikram"])
        notification = Notification.objects.filter(user=self.manager).get()
        self.assertIn("WEB-1", notification.message)

    def test_author_mentioning_self_gets_no_notification(self):
        """Never notify the author."""
        self.comment("note to self @meera")
        self.assertEqual(self.mentioned_usernames(), [])

    def test_non_members_and_unknown_names_are_ignored_silently(self):
        """Outsiders are never notified and the response does not reveal who exists."""
        response = self.comment("@oscar @nobody_here hello")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.mentioned_usernames(), [])

    def test_mentions_are_case_insensitive(self):
        """@MANOJ matches the user manoj."""
        self.comment("@MANOJ look")
        self.assertEqual(self.mentioned_usernames(), ["manoj"])

    def test_editing_notifies_only_newly_mentioned_people(self):
        """Once per comment per user: re-saving a mention does not notify again."""
        comment_id = self.comment("@manoj first").json()["id"]
        response = self.client_for(self.member).patch(
            f"{self.url}{comment_id}/", {"body": "@manoj and now @olivia"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["edited_at"])
        self.assertEqual(self.mentioned_usernames(), ["manoj", "olivia"])


class CommentPermissionTests(CommentTestCase):
    def test_viewer_cannot_comment_and_outsider_gets_404(self):
        """Matrix: viewers read but do not comment."""
        self.assertEqual(self.comment("hi", self.viewer).status_code, 403)
        self.assertEqual(self.comment("hi", self.outsider).status_code, 404)
        self.assertEqual(self.client_for(self.viewer).get(self.url).status_code, 200)

    def test_only_author_can_edit(self):
        """Even a manager cannot rewrite someone else's words."""
        comment_id = self.comment("mine").json()["id"]
        response = self.client_for(self.manager).patch(f"{self.url}{comment_id}/", {"body": "x"})
        self.assertEqual(response.status_code, 403)

    def test_author_and_manager_can_delete_but_other_member_cannot(self):
        """Authors and managers delete; other members get 403."""
        other_member = make_user("mira")
        from core.testing import add_member

        add_member(self.project, other_member, "member")
        first = self.comment("first").json()["id"]
        second = self.comment("second").json()["id"]
        self.assertEqual(
            self.client_for(other_member).delete(f"{self.url}{first}/").status_code, 403
        )
        self.assertEqual(
            self.client_for(self.member).delete(f"{self.url}{first}/").status_code, 204
        )
        self.assertEqual(
            self.client_for(self.manager).delete(f"{self.url}{second}/").status_code, 204
        )

    def test_soft_deleted_comment_shows_placeholder_and_cannot_be_edited(self):
        """Deleted comments stay in the thread as "[deleted]" with no text."""
        comment_id = self.comment("secret plan").json()["id"]
        self.client_for(self.member).delete(f"{self.url}{comment_id}/")
        listed = self.client_for(self.viewer).get(self.url).json()["results"][0]
        self.assertEqual(listed["body"], "[deleted]")
        self.assertTrue(listed["is_deleted"])
        self.assertEqual(Comment.objects.get(pk=comment_id).body, "")
        edit = self.client_for(self.member).patch(f"{self.url}{comment_id}/", {"body": "again"})
        self.assertEqual(edit.status_code, 400)

    def test_comment_from_other_task_is_not_reachable(self):
        """IDOR: a comment id only works under its own task."""
        Task.objects.create(project=self.project, number=2, title="Other", reporter=self.owner)
        comment_id = self.comment("on task 1").json()["id"]
        other_url = f"/api/projects/{self.project.pk}/tasks/2/comments/{comment_id}/"
        self.assertEqual(self.client_for(self.member).delete(other_url).status_code, 404)

    def test_empty_comment_is_rejected(self):
        """Blank bodies are not stored."""
        self.assertEqual(self.comment("   ").status_code, 400)

    def test_archived_project_rejects_comments(self):
        """Archived projects are read-only."""
        Project.objects.filter(pk=self.project.pk).update(status="archived")
        self.assertEqual(self.comment("late").status_code, 403)

    def test_html_is_stored_as_text_not_executed(self):
        """The API returns the raw text; templates autoescape it."""
        body = "<script>alert(1)</script>"
        response = self.comment(body)
        self.assertEqual(response.json()["body"], body)
