from .client import LLMClient
from .conf import HIRSCHAI_PROVIDER


def get_client(
    provider: str | None = None, *, user=None, model: str | None = None
) -> LLMClient:
    return LLMClient(provider, user=user, model=model)


def complete(
    prompt=None,
    *,
    messages=None,
    executor=None,
    provider=None,
    model=None,
    user=None,
    **kwargs,
) -> str:
    """`executor` is the pipeline path — the run's Executor carries provider+model+
    user as one value (the single-executor invariant). The loose provider/model/user
    kwargs stay for callers outside a run (llm_check, the config check endpoint)."""
    if executor is not None:
        return executor.complete(prompt=prompt, messages=messages, **kwargs)
    return get_client(provider, user=user, model=model).complete(
        prompt=prompt, messages=messages, **kwargs
    )


def stream(
    prompt=None,
    *,
    messages=None,
    executor=None,
    provider=None,
    model=None,
    user=None,
    **kwargs,
):
    if executor is not None:
        return executor.stream(prompt=prompt, messages=messages, **kwargs)
    return get_client(provider, user=user, model=model).stream(
        prompt=prompt, messages=messages, **kwargs
    )


def embed(inputs: list[str]) -> list[list[float]]:
    """Embed on the tower. Embedding is a HirschAI-only capability by design:
    commercial executors never see embedding work (and never could route it here
    without an explicit call site — the privacy grep in Verification checks that)."""
    return get_client(HIRSCHAI_PROVIDER).embed(inputs)
