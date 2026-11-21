"""Personal access tokens for the Claude skill: minting, and the DRF authentication that reads
them. The class goes on the skill views only, so a leaked token reaches the career DB and the
applications, never the account, the API keys or the rest of the API."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from jac.models import AccessToken

KEY_PREFIX = "jac_"
# A write per request is wasted on a skill that fires five calls in a row.
_TOUCH_EVERY = timedelta(minutes=1)


def hash_key(key: str) -> str:
    # The key is 32 random bytes, so a plain digest is enough: there is nothing to brute-force.
    return hashlib.sha256(key.encode()).hexdigest()


def mint_token(user, name: str = "") -> tuple[AccessToken, str]:
    """A new token row and its key. The key exists only in this return value."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    token = AccessToken.objects.create(
        user=user, name=name, prefix=key[:12], key_hash=hash_key(key)
    )
    return token, key


class AccessTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer jac_…`. No header: None, so session auth gets its turn. A header
    that names a token which doesn't exist (revoked, mistyped) is a 401, not a fallthrough."""

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Expected 'Authorization: Bearer <token>'.")
        return self.authenticate_key(parts[1].decode(errors="replace"))

    def authenticate_key(self, key: str):
        token = (
            AccessToken.objects.select_related("user")
            .filter(key_hash=hash_key(key))
            .first()
            if key.startswith(KEY_PREFIX)
            else None
        )
        if token is None or not token.user.is_active:
            raise AuthenticationFailed("Unknown or revoked token.")
        now = timezone.now()
        if token.last_used_at is None or now - token.last_used_at > _TOUCH_EVERY:
            AccessToken.objects.filter(pk=token.pk).update(last_used_at=now)
        return token.user, token

    def authenticate_header(self, request):
        return 'Bearer realm="jac"'


class PathTokenAuthentication(AccessTokenAuthentication):
    """The key as the connector URL's last segment (`/api/jac/mcp/jac_…/`), for a Claude whose
    connector dialog offers no request headers. Second choice: a URL can land in a proxy's
    access log where a header doesn't (jac's own nginx and daphne log no paths, the lab's Caddy
    may), so the header goes first and wins."""

    def authenticate(self, request):
        kwargs = (getattr(request, "parser_context", None) or {}).get("kwargs") or {}
        key = kwargs.get("key")
        return self.authenticate_key(key) if key else None
