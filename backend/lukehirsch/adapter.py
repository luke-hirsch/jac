"""Auth-flow adapter that closes a harassment vector in allauth's default flow:
signing up with someone else's address triggers an 'account already exists'
email to that address, and signup is rate-limited by IP only. We dedupe by
caching a per-address flag so each victim receives at most one such mail per
cooldown window, regardless of how many IPs the attacker rotates through."""

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.core.cache import cache


class HarassmentResistantAccountAdapter(DefaultAccountAdapter):
    ACCOUNT_EXISTS_MAIL_COOLDOWN_SECS = 24 * 60 * 60  # 1 day

    def send_account_already_exists_mail(self, email: str) -> None:
        cache_key = f"account-exists-mailed:{email.lower()}"
        if cache.get(cache_key):
            return
        cache.set(cache_key, True, self.ACCOUNT_EXISTS_MAIL_COOLDOWN_SECS)
        super().send_account_already_exists_mail(email)

    def is_open_for_signup(self, request) -> bool:
        """Gate registration behind ACCOUNT_ALLOW_SIGNUPS (default False). This is a launch
        toggle, not a privacy stance: the flag opens when the public CV showcase ships. allauth
        calls this on both the classic and headless signup paths, so one override closes both."""
        return bool(getattr(settings, "ACCOUNT_ALLOW_SIGNUPS", False))
