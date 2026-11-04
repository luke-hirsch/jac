"""Fernet-based at-rest encryption for sensitive config values (API keys).

Key source: settings.LLM_ENCRYPTION_KEY (set from env LLM_ENCRYPTION_KEY).
The default in settings.py is an insecure dev placeholder; production MUST
override it with a 32-byte url-safe base64 key (Fernet.generate_key()).
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Build and cache the Fernet instance from settings.LLM_ENCRYPTION_KEY."""
    key = getattr(settings, "LLM_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "LLM_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'` and set it as "
            "LLM_ENCRYPTION_KEY in your environment."
        )
    if isinstance(key, str):
        key = key.encode("utf-8")
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            f"LLM_ENCRYPTION_KEY is not a valid Fernet key: {exc}"
        ) from exc


def encrypt(plaintext: str) -> str:
    """Return the Fernet-encrypted, URL-safe base64 ciphertext of `plaintext`."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet ciphertext produced by encrypt().

    Raises ImproperlyConfigured when the key has rotated since encryption.
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "Failed to decrypt stored value — LLM_ENCRYPTION_KEY likely "
            "differs from the key used at encryption time."
        ) from exc
