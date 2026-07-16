from .client import LLMClient
from .conf import HIRSCHAI_PROVIDER


def get_client(
    provider: str | None = None, *, user=None, model: str | None = None
) -> LLMClient:
    return LLMClient(provider, user=user, model=model)


def complete(
    prompt=None, *, messages=None, provider=None, model=None, user=None, **kwargs
) -> str:
    return get_client(provider, user=user, model=model).complete(
        prompt=prompt, messages=messages, **kwargs
    )


def stream(
    prompt=None, *, messages=None, provider=None, model=None, user=None, **kwargs
):
    return get_client(provider, user=user, model=model).stream(
        prompt=prompt, messages=messages, **kwargs
    )


def embed(inputs: list[str]) -> list[list[float]]:
    """Embed on the tower. Embedding is a HirschAI-only capability by design:
    commercial executors never see embedding work (and never could route it here
    without an explicit call site — the privacy grep in Verification checks that)."""
    return get_client(HIRSCHAI_PROVIDER).embed(inputs)
