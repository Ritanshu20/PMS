from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    """Custom user created before the first migration so it can evolve freely.

    `username` is used for login and @mentions; `email` is unique
    (case-insensitive) because members are added to projects by exact email.
    """

    email = models.EmailField()
    display_name = models.CharField(max_length=150, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.display_name or self.username
