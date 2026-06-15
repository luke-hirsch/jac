"""Public API for the llm_connector app.

Convenience wrappers around LLMClient so callers don't need to instantiate
the client directly for simple one-shot completions.

Pass `user=` on every user-driven call so the per-user LLMConfig is resolved
instead of falling back to settings.LLM["default"].
"""

from .client import LLMClient


def get_client(alias: str = "default", user=None) -> LLMClient:
    """Instantiate an LLMClient for the given alias and optional user."""
    return LLMClient(alias, user=user)


def complete(
    prompt: str | None = None,
    *,
    messages: list[dict] | None = None,
    alias: str = "default",
    user=None,
    **kwargs,
) -> str:
    """Send a single completion request and return the response text."""
    return get_client(alias, user=user).complete(
        prompt=prompt, messages=messages, **kwargs
    )


def stream(
    prompt: str | None = None,
    *,
    messages: list[dict] | None = None,
    alias: str = "default",
    user=None,
    **kwargs,
):
    """Stream a completion response, yielding text chunks as a generator."""
    return get_client(alias, user=user).stream(
        prompt=prompt, messages=messages, **kwargs
    )


def embed(
    inputs: list[str],
    *,
    alias: str = "default",
    user=None,
) -> list[list[float]]:
    """Return an embedding vector per input string for the given alias.

    Only providers with an embedding endpoint support this (today: Ollama).
    """
    return get_client(alias, user=user).embed(inputs)
