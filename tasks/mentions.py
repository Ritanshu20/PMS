"""
@mention parsing for comments.

Called by `tasks.comment_services`. Pure text work plus one query to find which
mentioned usernames are members of the project. Must never reveal whether a
username exists outside the project: unknown names are ignored silently.
"""

import re

from django.db.models.functions import Lower

from accounts.models import User
from projects.models import ProjectMember

# "@asha", "@ravi.k" ... but not the "@" inside an email like "a@b.com".
MENTION_PATTERN = re.compile(r"(?<![\w@.])@([A-Za-z0-9_.-]{3,150})")
TRAILING_PUNCTUATION = ".-_"


def extract_usernames(body: str) -> set[str]:
    """Lower-cased candidate usernames found in `body`.

    Example:
        "Thanks @Asha and @ravi.k." -> {"asha", "ravi.k.", "ravi.k"}
        (a trailing "." may be sentence punctuation, so both spellings are tried).
    """
    candidates: set[str] = set()
    for match in MENTION_PATTERN.finditer(body or ""):
        name = match.group(1).lower()
        candidates.add(name)
        candidates.add(name.rstrip(TRAILING_PUNCTUATION))
    candidates.discard("")
    return candidates


def mentioned_members(project_id: int, body: str, author_id: int | None) -> list[User]:
    """Project members mentioned in `body`, excluding the author, each user once.

    RULE: only members of this project can be notified; viewers are members too.
    """
    candidates = extract_usernames(body)
    if not candidates:
        return []
    memberships = (
        ProjectMember.objects.filter(project_id=project_id)
        .annotate(username_lower=Lower("user__username"))
        .filter(username_lower__in=candidates)
        .exclude(user_id=author_id)
        .select_related("user")
        .order_by("user__username")
    )
    return [membership.user for membership in memberships]
