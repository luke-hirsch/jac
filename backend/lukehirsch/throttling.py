"""Per-user throttles under a fixed scope, for views whose own `throttle_scope` doesn't fit."""

from rest_framework.permissions import SAFE_METHODS
from rest_framework.settings import api_settings
from rest_framework.throttling import UserRateThrottle


class _PerUserThrottle(UserRateThrottle):
    def get_rate(self):
        """Read live: the parent's THROTTLE_RATES is bound at import, out of reach of
        `override_settings`. A scope without a rate has no limit."""
        return api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)


class ChatDailyThrottle(_PerUserThrottle):
    """The chat's daily budget, beside the `llm-chat` scope that only bounds its pace."""

    scope = "llm-chat-daily"


class UploadThrottle(_PerUserThrottle):
    """Whatever puts a file on disk. These endpoints also read and edit plain fields, which cost
    nothing: only a request that carries a file counts."""

    scope = "uploads"

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS or not request.FILES:
            return True
        return super().allow_request(request, view)
