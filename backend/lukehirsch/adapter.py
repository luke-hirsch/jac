"""Auth-flow adapter that closes a harassment vector in allauth's default flow:
signing up with someone else's address triggers an 'account already exists'
email to that address, and signup is rate-limited by IP only. We dedupe by
caching a per-address flag so each victim receives at most one such mail per
cooldown window, regardless of how many IPs the attacker rotates through."""
from django.core.cache import cache
from allauth.account.adapter import DefaultAccountAdapter


class HarassmentResistantAccountAdapter(DefaultAccountAdapter):
    ACCOUNT_EXISTS_MAIL_COOLDOWN_SECS = 24 * 60 * 60  # 1 day

    def send_account_already_exists_mail(self, email: str) -> None:
        cache_key = f"account-exists-mailed:{email.lower()}"
        if cache.get(cache_key):
            return
        cache.set(cache_key, True, self.ACCOUNT_EXISTS_MAIL_COOLDOWN_SECS)
        super().send_account_already_exists_mail(email)
