"""Environment parsing + a boot-time production-secret guard.

Env vars are always strings; `os.getenv("DEBUG", True)` returns the string "False" for
`DEBUG=False`, which is truthy — the exact bug this module exists to prevent. Use `env_bool`
/ `env_int` for every boolean/int setting sourced from the environment.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

# The insecure placeholders shipped in settings.py for local dev. Production MUST override both;
# `verify_production_secrets` refuses to boot with DEBUG off while either is still in place.
DEV_SECRET_KEY = "django-insecure-g4pj@dk!pf+e#+8^5t-ic(avl(ng9e=@3%ziwrls-!5sq%y6s5"
DEV_ENCRYPTION_KEY = "ZGphbmdvLWluc2VjdXJlLWxsbS1kZXYta2V5LW9ubHk="
# A laptop's own Postgres over its socket, as the logged-in user. Production must set DATABASE_URL.
DEV_DATABASE_URL = "postgres:///jac"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def load_local_env(path: Path) -> None:
    """Export the KEY=value lines of a gitignored dev file, without overriding the real
    environment. The one thing it exists for: `DEBUG=1` on a laptop, now that a process with no
    DEBUG variable runs in production mode. The image never carries this file (.dockerignore), so
    a server only ever reads its real environment."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


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


def env_list(name: str, default: list[str]) -> list[str]:
    """Parse a comma-separated env var into a list of stripped, non-empty strings.

    Missing var -> `default`. `FOO="a, b ,,c"` -> `["a", "b", "c"]`. An empty/whitespace-only
    value yields `[]` (an explicit "no entries"), not `default`.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def verify_production_secrets(
    *,
    debug: bool,
    secret_key: str,
    encryption_key: str,
    base_domain: str | None = None,
    database_url: str | None = None,
) -> None:
    """Raise ImproperlyConfigured if running with DEBUG off on a checked-in dev secret, or with
    BASE_DOMAIN (every absolute URL and ALLOWED_HOSTS derive from it) or DATABASE_URL still at its
    dev default.

    No-op in DEBUG (local dev is allowed to use the placeholders). Called once at the end of
    settings.py so a misconfigured production deploy fails fast on boot instead of silently
    serving forgeable sessions / decryptable API keys.
    """
    if debug:
        return
    offenders = []
    if not secret_key or secret_key == DEV_SECRET_KEY:
        offenders.append("SECRET_KEY")
    if not encryption_key or encryption_key == DEV_ENCRYPTION_KEY:
        offenders.append("LLM_ENCRYPTION_KEY")
    if offenders:
        raise ImproperlyConfigured(
            "Refusing to start with DEBUG off while these secrets are blank or still the "
            f"checked-in dev defaults: {', '.join(offenders)}. Set them from the "
            "environment (SECRET_KEY, LLM_ENCRYPTION_KEY). On a laptop, DEBUG=1 in "
            "backend/.env.local turns debug mode on."
        )
    if base_domain == "localhost":
        raise ImproperlyConfigured(
            "Refusing to start with DEBUG off while BASE_DOMAIN is unset: ALLOWED_HOSTS and "
            "every absolute URL derive from it. Set it to the bare domain, e.g. example.com."
        )
    if database_url == DEV_DATABASE_URL:
        raise ImproperlyConfigured(
            "Refusing to start with DEBUG off while DATABASE_URL is unset. Set it to "
            "postgres://user:password@host:5432/name, with the password URL-encoded."
        )
