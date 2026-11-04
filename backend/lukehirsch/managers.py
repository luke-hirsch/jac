"""Shared model managers used across apps.

`SystemScopedManager` backs the "system defaults + per-user rows" pattern: rows owned by
the ``settings.SYSTEM_USER_USERNAME`` sentinel are read-only defaults visible to everyone
(``jac.Domain``, ``jac.ApplicationLayout``, ``spa.PersonalityQuestion``); every other row
is private to its owner. It lives here — the config package that already holds shared
middleware/permissions — rather than in an app, so both ``jac`` and ``spa`` can share it
without an app-to-app import (``jac`` already depends on ``spa`` at runtime; a back-edge
would be a cycle smell).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class SystemScopedManager(models.Manager):
    def for_user(self, user):
        return self.filter(
            Q(user=user) | Q(user__username=settings.SYSTEM_USER_USERNAME)
        )

    def defaults(self):
        return self.filter(user__username=settings.SYSTEM_USER_USERNAME)
