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
        raise ImproperlyConfigured(
            f"LLM alias '{alias}' is missing a 'provider' key."
        )
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
            getattr(user, "pk", user), alias, FALLBACK_ALIAS,
        )
        return _global_fallback()
    return cfg.to_config_dict()


def logging_enabled() -> bool:
    return bool(getattr(settings, "LLM_LOGGING", False))
