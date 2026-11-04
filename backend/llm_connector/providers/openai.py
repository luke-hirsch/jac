from collections.abc import Generator

from django.core.exceptions import ImproperlyConfigured

from ..base import LLMAdapter
from ..registry import register


@register("openai")
class OpenAIAdapter(LLMAdapter):
    """Adapter for the OpenAI Chat Completions API."""

    supports_web_search = True

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
        self._model = config.get("model", "gpt-5.6-luna")
        self._max_tokens = config.get("max_tokens")
        self._reasoning_effort = config.get("reasoning_effort")

    def map_params(self, params: dict) -> dict:
        out = {}
        if params.get("effort"):
            out["reasoning_effort"] = params["effort"]
        return out

    def _apply_model_params(self, params: dict) -> None:
        if self._max_tokens:
            params.setdefault("max_completion_tokens", self._max_tokens)
        if self._reasoning_effort:
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

    def web_search(self, messages: list[dict], **kwargs) -> dict:
        """Run a completion with OpenAI's native web search (Responses API).

        Web search is a Responses-API server-side tool, not available on chat.completions
        for general models — hence responses.create here, not the complete() path. For
        gpt-5.x set reasoning_effort='high' in the LLMConfig (recommended for web search;
        gpt-5 with 'minimal' reasoning is unsupported).
        """
        effort = kwargs.pop("reasoning_effort", self._reasoning_effort)
        kwargs.pop(
            "max_uses", None
        )  # Anthropic-only search cap; not a Responses-API param
        params: dict = dict(
            model=self._model,
            input=messages,  # Responses API takes `input`, not `messages`
            tools=[{"type": "web_search"}],
            **kwargs,
        )
        if self._max_tokens:
            params.setdefault("max_output_tokens", self._max_tokens)
        if effort:
            params.setdefault(
                "reasoning", {"effort": effort}
            )  # gpt-5.x reasoning models only
        response = self._client.responses.create(**params)

        sources: list[str] = []
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "message":
                continue
            for block in getattr(item, "content", None) or []:
                for ann in getattr(block, "annotations", None) or []:
                    if getattr(ann, "type", None) == "url_citation":
                        url = getattr(ann, "url", None)
                        if url:
                            sources.append(url)
        return {
            "text": (getattr(response, "output_text", "") or "").strip(),
            "sources": list(dict.fromkeys(sources)),  # dedupe, keep order
        }
