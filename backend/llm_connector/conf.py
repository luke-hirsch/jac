import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

FALLBACK_ALIAS = "default"


def get_llm_settings() -> dict:
    llm = getattr(settings, "LLM", None)
    if not llm:
        raise ImproperlyConfigured(
            "LLM setting is missing. Add an LLM dict to your settings.py."
        )
    return llm


def _settings_config(alias: str) -> dict:
    llm = get_llm_settings()
    if alias not in llm:
        raise ImproperlyConfigured(
            f"LLM alias '{alias}' not found in settings.LLM. "
            f"Available aliases: {list(llm.keys())}"
        )
    config = llm[alias]
    if "provider" not in config:
        raise ImproperlyConfigured(f"LLM alias '{alias}' is missing a 'provider' key.")
    return config


def _global_fallback() -> dict:
    """Return the global zero-cost fallback config. Raises if it's not
    configured — settings.LLM[FALLBACK_ALIAS] is load-bearing."""
    llm = get_llm_settings()
    if FALLBACK_ALIAS not in llm:
        raise ImproperlyConfigured(
            f"settings.LLM[{FALLBACK_ALIAS!r}] is required. It is the "
            "zero-cost fallback served to users without a personal LLMConfig."
        )
    return _settings_config(FALLBACK_ALIAS)


def get_alias_config(alias: str = "default", user=None) -> dict:
    """Resolve a config dict for the given alias.

    With `user`: look up `LLMConfig(user=user, alias=alias)`. If absent, log
    a warning and fall back to settings.LLM["default"] (the zero-cost Ollama
    config) so the call never silently bills the site owner's API keys.

    Without `user` (CLI / internal jobs): read directly from settings.LLM[alias].
    In production settings.LLM only contains "default"; tests may override it.
    """
    if user is None:
        return _settings_config(alias)

    from .models import LLMConfig

    try:
        cfg = LLMConfig.objects.get(user=user, alias=alias)
    except LLMConfig.DoesNotExist:
        logger.warning(
            "No LLMConfig for user=%s alias=%r — falling back to settings.LLM[%r].",
            getattr(user, "pk", user),
            alias,
            FALLBACK_ALIAS,
        )
        return _global_fallback()
    return cfg.to_config_dict()


# Providers that cost nothing to call — self-hosted or user-hosted endpoints. The
# paid trio (anthropic/openai/google) bills per token; pick_alias(free_only=True)
# refuses to route a rung onto them (e.g. a "light" showcase run must stay free).
FREE_PROVIDERS = {"ollama", "custom"}


def is_free_alias(alias: str, user=None) -> bool:
    """True when the alias resolves to a zero-cost provider (see FREE_PROVIDERS).
    Unresolvable aliases count as NOT free — never route cost-guarded work at an
    unknown target."""
    try:
        config = get_alias_config(alias, user=user)
    except Exception:  # noqa: BLE001 — missing/broken config
        return False
    return config.get("provider", "") in FREE_PROVIDERS


def get_pinned_alias(role: str, user=None) -> str | None:
    """The user's pinned favourite alias for a support role, or None.

    A stale pin — its alias is neither one of the user's LLMConfig rows nor the
    "default" fallback (the row was deleted or renamed) — is ignored, so the
    caller falls back exactly as if nothing were pinned. (`get_alias_config`
    can't make that call: with a user it silently resolves any unknown alias to
    the default config.)
    """
    from .models import LLMConfig, LLMPin

    if user is None or role not in LLMPin.Role.values:
        return None
    pin = LLMPin.objects.filter(user=user, role=role).first()
    if pin is None:
        return None
    if (
        pin.alias != FALLBACK_ALIAS
        and not LLMConfig.objects.filter(user=user, alias=pin.alias).exists()
    ):
        logger.warning(
            "Pinned alias %r for user=%s no longer resolves — ignoring the pin.",
            pin.alias,
            getattr(user, "pk", user),
        )
        return None
    return pin.alias


def pick_alias(
    preferred_pin: str | None,
    *,
    fallback: str,
    user=None,
    free_only: bool = False,
) -> str:
    """Resolve the alias a pipeline rung should run on.

    A rung declares the support role it wants (its PREFERRED_PIN); when the user pinned
    a favourite model for that role, the rung runs there instead of on the run's main
    alias — that is how a conversational run keeps its audits off the expensive model.
    `free_only=True` (the free-alias cost guard) refuses a pin that resolves to a paid
    provider. No preference, no pin, or a refused pin -> `fallback` (normally the run's
    main alias).
    """
    if not preferred_pin:
        return fallback
    pinned = get_pinned_alias(preferred_pin, user=user)
    if pinned is None:
        return fallback
    if free_only and not is_free_alias(pinned, user=user):
        return fallback
    return pinned


def get_embed_floors(alias: str = "default", user=None) -> dict:
    """Per-section cosine drop floors for the light rung, from the resolved config's
    `embed_floors` key. {} when unset or on any resolution error — the caller merges
    these over its own defaults. Floors are embedder-specific (cosine distributions
    differ between models), so they live with the model config, not hardcoded in the
    filter.
    """
    try:
        config = get_alias_config(alias, user=user)
    except Exception:  # noqa: BLE001 — missing/broken config -> defaults
        return {}
    floors = config.get("embed_floors")
    return floors if isinstance(floors, dict) else {}


def logging_enabled() -> bool:
    return bool(getattr(settings, "LLM_LOGGING", False))
