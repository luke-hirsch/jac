from django.core.exceptions import ImproperlyConfigured

from .base import LLMAdapter

_registry: dict[str, type[LLMAdapter]] = {}


def register(name: str):
    """Class decorator that registers an LLMAdapter subclass under `name`."""

    def decorator(cls: type[LLMAdapter]):
        _registry[name] = cls
        return cls

    return decorator


def get_adapter_class(provider: str) -> type[LLMAdapter]:
    """Return the adapter class for the given provider name.

    Built-in providers are lazy-loaded on first use so that missing optional
    SDK packages (anthropic, openai, google-generativeai) only raise when the
    provider is actually requested, not at import time.

    Raises ImproperlyConfigured for unknown providers.
    """
    if provider not in _registry:
        _load_builtin(provider)

    if provider not in _registry:
        raise ImproperlyConfigured(
            f"Unknown LLM provider '{provider}'. "
            f"Registered providers: {list(_registry.keys())}"
        )
    return _registry[provider]


def _load_builtin(provider: str):
    """Import the built-in provider module so its @register decorator fires."""
    builtin = {
        "anthropic": "llm_connector.providers.anthropic",
        "openai": "llm_connector.providers.openai",
        "google": "llm_connector.providers.google",
        "custom": "llm_connector.providers.custom",
        "ollama": "llm_connector.providers.ollama",
    }
    if provider in builtin:
        import importlib

        importlib.import_module(builtin[provider])
