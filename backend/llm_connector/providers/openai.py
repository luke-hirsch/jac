import re
from collections.abc import Generator

from django.core.exceptions import ImproperlyConfigured

from ..base import LLMAdapter, translated_sdk_errors
from ..registry import register

# The list names every model the key reaches: embeddings, audio, images, moderation, and more.
# This adapter speaks Chat Completions, so only chat text models stay.
_CHAT_MODEL = re.compile(r"^(gpt-\d|o\d|chatgpt-)")
_NOT_CHAT = (
    "audio",
    "realtime",
    "tts",
    "transcribe",
    "whisper",
    "image",
    "dall-e",
    "embedding",
    "moderation",
    "search",
    "instruct",
    "codex",
    "-pro",
    "deep-research",
    "computer-use",
)
# The list carries no capabilities, so effort goes by family: the o-series and gpt-5 onwards
# reason; gpt-4.x and the chat snapshots answer reasoning_effort with a 400.
_REASONING = re.compile(r"^(o\d|gpt-([5-9]|\d{2,}))")
_EFFORT = {"effort": {"choices": ["low", "medium", "high"]}}


@register("openai")
class OpenAIAdapter(LLMAdapter):
    """Adapter for the OpenAI Chat Completions API. A call needs the config's `model` (the
    run's pick from the key's model list); listing models doesn't."""

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
        self._sdk = _openai
        self._client = _openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.get("timeout", _openai.DEFAULT_TIMEOUT),
            max_retries=config.get("max_retries", _openai.DEFAULT_MAX_RETRIES),
        )
        self._model = config.get("model") or ""
        self._max_tokens = config.get("max_tokens")
        self._reasoning_effort = config.get("reasoning_effort")

    def _require_model(self) -> str:
        if not self._model:
            raise ImproperlyConfigured(
                "LLM openai provider requires a 'model' in its config: a run names one "
                "from the key's model list."
            )
        return self._model

    def list_models(self) -> list[dict]:
        with translated_sdk_errors(self._sdk, "openai"):
            listed = list(self._client.models.list())
        chat = [m for m in listed if self._is_chat_model(m.id)]
        chat.sort(key=lambda m: getattr(m, "created", 0) or 0, reverse=True)
        return [
            {
                "id": m.id,
                "label": m.id,
                "knobs": dict(_EFFORT) if self._reasons(m.id) else {},
            }
            for m in chat
        ]

    @staticmethod
    def _is_chat_model(model_id: str) -> bool:
        return bool(_CHAT_MODEL.match(model_id)) and not any(
            part in model_id for part in _NOT_CHAT
        )

    @staticmethod
    def _reasons(model_id: str) -> bool:
        return bool(_REASONING.match(model_id)) and "chat" not in model_id

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
        params: dict = dict(model=self._require_model(), messages=messages, **kwargs)
        self._apply_model_params(params)
        with translated_sdk_errors(self._sdk, "openai"):
            response = self._client.chat.completions.create(**params)
        return response.choices[0].message.content

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        params: dict = dict(
            model=self._require_model(), messages=messages, stream=True, **kwargs
        )
        self._apply_model_params(params)
        with translated_sdk_errors(self._sdk, "openai"):
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
            model=self._require_model(),
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
        with translated_sdk_errors(self._sdk, "openai"):
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
