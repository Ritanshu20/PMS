"""
Account services: registration and logout.

Called by `accounts/api_views.py` (and later by template views). Must never
return or log passwords or tokens.
"""

import logging

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from core.exceptions import BusinessRuleError

logger = logging.getLogger(__name__)


def register_user(*, username: str, email: str, password: str, display_name: str = "") -> User:
    """Create a new user after validating uniqueness and password strength.

    Why: email must be unique case-insensitively because members are added by email.

    Raises:
        ValidationError: duplicate username/email or weak password.
    """
    email = User.objects.normalize_email(email).strip()
    errors: dict[str, list[str]] = {}
    if User.objects.filter(username__iexact=username).exists():
        errors["username"] = ["A user with this username already exists."]
    if User.objects.filter(email__iexact=email).exists():
        errors["email"] = ["A user with this email already exists."]
    candidate = User(username=username, email=email, display_name=display_name)
    try:
        validate_password(password, user=candidate)
    except ValidationError as exc:
        errors["password"] = list(exc.messages)
    if errors:
        raise ValidationError(errors)

    with transaction.atomic():
        candidate.set_password(password)
        candidate.save()
    logger.info("user_registered user_id=%s", candidate.pk)
    return candidate


def logout_user(*, refresh_token: str) -> None:
    """Blacklist a refresh token so it can never be used again.

    Raises:
        BusinessRuleError: token is invalid, expired or already blacklisted.
    """
    try:
        RefreshToken(refresh_token).blacklist()
    except TokenError as exc:
        raise BusinessRuleError("Invalid or expired refresh token.", code="invalid_token") from exc
