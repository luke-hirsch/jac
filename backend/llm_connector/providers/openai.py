import re
from collections.abc import Generator

from django.core.exceptions import ImproperlyConfigured

from ..base import LLMAdapter
from ..registry import register

_REASONING_MODEL_RE = re.compile(r"^o\d")


@register("openai")
class OpenAIAdapter(LLMAdapter):
    """Adapter for the OpenAI Chat Completions API.

    Handles reasoning models (o1, o3, …) transparently: uses max_completion_tokens
    instead of max_tokens and forwards reasoning_effort when configured.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        try:
            import openai as _openai
        except ImportError:
            raise ImproperlyConfigured(
                "The 'openai' package is required for the openai provider. "
                "Run: pip install openai"
            )
        api_key = config.get("api_key")
        if not api_key:
            raise ImproperlyConfigured(
                "LLM openai provider requires an 'api_key' in its config."
            )
        base_url = config.get("url")
        self._client = _openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = config.get("model", "gpt-4o")
        self._max_tokens = config.get("max_tokens")
        self._reasoning_effort = config.get("reasoning_effort")

    def _is_reasoning_model(self) -> bool:
        """True when the configured model name matches the o1/o3/… naming pattern."""
        return bool(_REASONING_MODEL_RE.match(self._model))

    def _apply_model_params(self, params: dict) -> None:
        """Inject token-limit and reasoning-effort params appropriate for the model family."""
        if self._max_tokens:
            key = "max_completion_tokens" if self._is_reasoning_model() else "max_tokens"
            params.setdefault(key, self._max_tokens)
        if self._reasoning_effort and self._is_reasoning_model():
            params.setdefault("reasoning_effort", self._reasoning_effort)

    def complete(self, messages: list[dict], **kwargs) -> str:
        params: dict = dict(model=self._model, messages=messages, **kwargs)
        self._apply_model_params(params)
        response = self._client.chat.completions.create(**params)
        return response.choices[0].message.content

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        params: dict = dict(model=self._model, messages=messages, stream=True, **kwargs)
        self._apply_model_params(params)
        for chunk in self._client.chat.completions.create(**params):
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def token_counts(self, response) -> tuple[int | None, int | None]:
        usage = getattr(response, "usage", None)
        if usage:
            return usage.prompt_tokens, usage.completion_tokens
        return None, None
