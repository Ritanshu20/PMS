"""
The single source of "now" for business logic.

Why: tests must control time (due dates, completed_at, burndown days). Every
service calls `core.clock.now()` / `core.clock.today()` instead of
`timezone.now()`, so a test can patch one place: `mock.patch("core.clock.now")`.
`today()` calls `now()`, so patching `now` also moves `today`.
"""

from datetime import date, datetime

from django.utils import timezone


def now() -> datetime:
    """Return the current aware datetime (UTC)."""
    return timezone.now()


def today() -> date:
    """Return today's date in the project timezone (settings.TIME_ZONE, Asia/Kolkata)."""
    return timezone.localdate(now())
