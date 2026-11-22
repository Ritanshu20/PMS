"""
Server-rendered account pages. Registration uses the same service as the API
(`accounts.services.register_user`); login/logout use Django's session auth.
The login page counts failed attempts per IP to slow down password guessing.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from accounts import services
from accounts.forms import RegisterForm

logger = logging.getLogger(__name__)
TOO_MANY_ATTEMPTS = "Too many failed logins from your network. Please try again in 15 minutes."


class ThrottledLoginView(LoginView):
    """Django's LoginView plus a failed-attempt limit per client IP.

    SAFETY: after LOGIN_FAILURE_LIMIT failures within the window, even a correct
    password is refused until the window passes (the attacker can't tell).
    """

    redirect_authenticated_user = True

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if _failure_count(request) >= settings.LOGIN_FAILURE_LIMIT:
            # A fresh empty form: the submitted password is never checked.
            empty_form = self.get_form_class()(request=request)
            context = self.get_context_data(form=empty_form, throttle_error=TOO_MANY_ATTEMPTS)
            return self.render_to_response(context, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form) -> HttpResponse:
        _record_failure(self.request)
        return super().form_invalid(form)


def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("web-project-list")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = services.register_user(
                username=data["username"],
                email=data["email"],
                password=data["password"],
                display_name=data["display_name"],
            )
        except ValidationError as error:
            for field, field_errors in error.message_dict.items():
                for message in field_errors:
                    form.add_error(field if field in form.fields else None, message)
        else:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, f"Welcome, {user.username}!")
            return redirect("web-project-list")
    return render(request, "accounts/register.html", {"form": form})


def _failure_key(request: HttpRequest) -> str:
    # REMOTE_ADDR only: X-Forwarded-For can be forged unless a trusted proxy sets it.
    return f"login-failures:{request.META.get('REMOTE_ADDR', 'unknown')}"


def _failure_count(request: HttpRequest) -> int:
    return cache.get(_failure_key(request), 0)


def _record_failure(request: HttpRequest) -> None:
    key = _failure_key(request)
    if not cache.add(key, 1, settings.LOGIN_FAILURE_WINDOW_SECONDS):
        cache.incr(key)
    logger.warning("login_failed ip=%s", request.META.get("REMOTE_ADDR", "unknown"))
