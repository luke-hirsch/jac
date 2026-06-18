import logging
import re

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


_STRENGTHS = {"light", "standard", "strong"}
# Parameter-size token in a model id, e.g. "0.8b" in "qwen3.5:0.8b" or "70b".
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b")
# Cloud model names without a size token that still signal a small/fast tier.
_SMALL_NAME_HINTS = ("haiku", "mini", "nano", "lite", "flash")
# Embedding-model name hints. These map to 'light' regardless of size token — an
# embedder only ever does the light rung, and large embedders (e5-mistral-7b) must
# not autodetect to 'standard'. Tune as new embedders show up.
_EMBED_NAME_HINTS = ("embed", "bge", "gte", "e5", "minilm", "nomic")


def _autodetect_strength(provider: str, model: str) -> str:
    """Best-effort capability guess from a model id, for aliases with no explicit
    `strength`. Conservative: anything we can't positively identify as small ->
    'strong' (the full ladder), so paid configs and existing tests keep their
    behaviour. Only models we recognise as small get opted down.
    """
    name = (model or "").lower()
    if any(hint in name for hint in _EMBED_NAME_HINTS):
        return "light"
    sizes = [float(m) for m in _SIZE_RE.findall(name)]
    if sizes:
        size = max(sizes)
        if size <= 3:
            return "light"
        if size <= 14:
            return "standard"
        return "strong"
    if any(hint in name for hint in _SMALL_NAME_HINTS):
        return "standard"
    return "strong"


def get_alias_strength(alias: str = "default", user=None) -> str:
    """Pipeline capability hint for an alias: 'light' | 'standard' | 'strong'.

    An explicit, valid `strength` in the resolved config (LLMConfig.extra for
    per-user rows, the settings.LLM dict for the default) always wins. Otherwise
    auto-detect from the model id. Unknown -> 'strong' (full ladder), preserving
    prior behaviour for anything we don't recognise.
    """
    try:
        config = get_alias_config(alias, user=user)
    except Exception:  # noqa: BLE001 — missing/broken config -> safe default
        return "strong"
    strength = config.get("strength")
    if strength in _STRENGTHS:
        return strength
    return _autodetect_strength(config.get("provider", ""), config.get("model", ""))


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
