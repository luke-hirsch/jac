"""Environment parsing + a boot-time production-secret guard.

Env vars are always strings; `os.getenv("DEBUG", True)` returns the string "False" for
`DEBUG=False`, which is truthy — the exact bug this module exists to prevent. Use `env_bool`
/ `env_int` for every boolean/int setting sourced from the environment.
"""

import os

from django.core.exceptions import ImproperlyConfigured

# The insecure placeholders shipped in settings.py for local dev. Production MUST override both;
# `verify_production_secrets` refuses to boot with DEBUG off while either is still in place.
DEV_SECRET_KEY = "django-insecure-g4pj@dk!pf+e#+8^5t-ic(avl(ng9e=@3%ziwrls-!5sq%y6s5"
DEV_ENCRYPTION_KEY = "ZGphbmdvLWluc2VjdXJlLWxsbS1kZXYta2V5LW9ubHk="

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var. Accepts 1/true/yes/on and 0/false/no/off (case-insensitive).

    Missing var -> `default`. An already-boolean default is returned as-is when unset, so
    `env_bool("DEBUG", True)` keeps working. An unrecognised value falls back to `default`.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    return default


def env_int(name: str, default: int) -> int:
    """Parse an int env var. Missing or non-integer -> `default`."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def verify_production_secrets(
    *, debug: bool, secret_key: str, encryption_key: str
) -> None:
    """Raise ImproperlyConfigured if running with DEBUG off on a checked-in dev secret.

    No-op in DEBUG (local dev is allowed to use the placeholders). Called once at the end of
    settings.py so a misconfigured production deploy fails fast on boot instead of silently
    serving forgeable sessions / decryptable API keys.
    """
    if debug:
        return
    offenders = []
    if secret_key == DEV_SECRET_KEY:
        offenders.append("SECRET_KEY")
    if encryption_key == DEV_ENCRYPTION_KEY:
        offenders.append("LLM_ENCRYPTION_KEY")
    if offenders:
        raise ImproperlyConfigured(
            "Refusing to start with DEBUG off while these secrets are still the "
            f"checked-in dev defaults: {', '.join(offenders)}. Set them from the "
            "environment (SECRET_KEY, LLM_ENCRYPTION_KEY)."
        )
