from collections.abc import Generator

from django.core.exceptions import ImproperlyConfigured

from ..base import LLMAdapter, translated_sdk_errors
from ..registry import register

# Every effort level the Models API reports on, lowest first.
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def _supported(node, *path) -> bool:
    """`capabilities.<path>.supported`, False wherever the provider left a step out. The SDK
    hands back typed objects; a shape it couldn't type stays a dict, so both are read."""
    for key in (*path, "supported"):
        if node is None:
            return False
        node = node.get(key) if isinstance(node, dict) else getattr(node, key, None)
    return node is True


@register("anthropic")
class AnthropicAdapter(LLMAdapter):
    """Adapter for the Anthropic Messages API.

    Requires the `anthropic` package and an api_key in the config. A call also needs the
    config's `model` (the run's pick from the key's model list); listing models doesn't.
    Splits role=system messages out into the top-level `system` param,
    which the Anthropic API requires instead of a system turn in the messages list.
    """

    supports_web_search = True

    def __init__(self, config: dict):
        super().__init__(config)
        try:
            import anthropic as _anthropic
        except ImportError:
            raise ImproperlyConfigured(
                "The 'anthropic' package is required for the anthropic provider. "
                "Run: pip install anthropic"
            )
        api_key = config.get("api_key")
        if not api_key:
            raise ImproperlyConfigured(
                "LLM anthropic provider requires an 'api_key' in its config."
            )
        self._sdk = _anthropic
        self._client = _anthropic.Anthropic(
            api_key=api_key,
            timeout=config.get("timeout", _anthropic.DEFAULT_TIMEOUT),
            max_retries=config.get("max_retries", _anthropic.DEFAULT_MAX_RETRIES),
        )
        self._model = config.get("model") or ""
        self._max_tokens = config.get("max_tokens", 4096)
        # How this model thinks, copied from its stored list entry: "adaptive", "budget", None.
        self._thinking = config.get("thinking")

    def _require_model(self) -> str:
        if not self._model:
            raise ImproperlyConfigured(
                "LLM anthropic provider requires a 'model' in its config: a run names one "
                "from the key's model list."
            )
        return self._model

    def list_models(self) -> list[dict]:
        # Iterating the page fetches the next ones, so it stays inside the translation.
        with translated_sdk_errors(self._sdk, "anthropic"):
            infos = list(self._client.models.list(limit=100))
        return [self._model_row(info) for info in infos]

    @classmethod
    def _model_row(cls, info) -> dict:
        """One listed model as the stored row. Effort is offered where the model takes it: the
        levels it reports on the adaptive path, the three budgets on the old one (Haiku 4.5
        thinks on a token budget and takes no effort). No capability data means no knobs."""
        caps = getattr(info, "capabilities", None)
        levels = (
            [lvl for lvl in _EFFORT_LEVELS if _supported(caps, "effort", lvl)]
            if _supported(caps, "effort")
            else []
        )
        thinks = _supported(caps, "thinking")
        if thinks and levels and _supported(caps, "thinking", "types", "adaptive"):
            thinking, choices = "adaptive", levels
        elif thinks and _supported(caps, "thinking", "types", "enabled"):
            thinking, choices = "budget", list(cls._THINKING_BUDGET)
        else:
            thinking, choices = None, []
        return {
            "id": info.id,
            "label": getattr(info, "display_name", None) or info.id,
            "knobs": {"effort": {"choices": choices}} if choices else {},
            "thinking": thinking,
        }

    # Thinking room per effort level, reserved ON TOP of the configured output room: the API
    # counts thinking against max_tokens, and the budget path needs budget_tokens < max_tokens.
    # The top stays at 16384 so a call without streaming stays under the SDK's ten-minute
    # estimate (about 21k max_tokens), which it refuses to send otherwise.
    _THINKING_BUDGET = {"low": 2048, "medium": 8192, "high": 16384}
    _ADAPTIVE_ROOM = {**_THINKING_BUDGET, "xhigh": 16384, "max": 16384}

    def map_params(self, params: dict) -> dict:
        effort = params.get("effort")
        if not effort or self._thinking not in ("adaptive", "budget"):
            return {}
        if self._thinking == "adaptive":
            # Every model with adaptive thinking answers budget_tokens with a 400, and Opus
            # 4.7/4.8 don't think unless asked: both halves go, and effort sits inside
            # output_config, not at the top level.
            room = self._ADAPTIVE_ROOM.get(effort, 8192)
            return {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": effort},
                "max_tokens": room + self._max_tokens,
            }
        budget = self._THINKING_BUDGET.get(effort, 8192)
        return {
            "thinking": {"type": "enabled", "budget_tokens": budget},
            "max_tokens": budget + self._max_tokens,
        }

    @staticmethod
    def _text_from(response) -> str:
        """Join all text blocks, skipping tool_use / thinking / web_search blocks. Anthropic can
        return a non-text block first (e.g. with tools enabled), so indexing content[0] is unsafe."""
        return "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()

    @staticmethod
    def _split_system(
        messages: list[dict], extra_system: str | None
    ) -> tuple[str | None, list[dict]]:
        """Pull role=system messages out of the list; Anthropic requires a top-level param."""
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        api_msgs = [m for m in messages if m.get("role") != "system"]
        if extra_system:
            system_parts.insert(0, extra_system)
        system = "\n\n".join(system_parts) if system_parts else None
        return system, api_msgs

    def complete(self, messages: list[dict], **kwargs) -> str:
        system, api_msgs = self._split_system(
            messages, kwargs.pop("system", self.config.get("system"))
        )
        params = {
            "model": self._require_model(),
            "max_tokens": self._max_tokens,
            "messages": api_msgs,
        }
        params.update(kwargs)
        if system:
            params["system"] = system
        with translated_sdk_errors(self._sdk, "anthropic"):
            response = self._client.messages.create(**params)
        return self._text_from(response)

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        system, api_msgs = self._split_system(
            messages, kwargs.pop("system", self.config.get("system"))
        )
        params = {
            "model": self._require_model(),
            "max_tokens": self._max_tokens,
            "messages": api_msgs,
        }
        params.update(kwargs)
        if system:
            params["system"] = system
        with (
            translated_sdk_errors(self._sdk, "anthropic"),
            self._client.messages.stream(**params) as s,
        ):
            yield from s.text_stream

    def token_counts(self, response) -> tuple[int | None, int | None]:
        usage = getattr(response, "usage", None)
        if usage:
            return usage.input_tokens, usage.output_tokens
        return None, None

    def web_search(self, messages: list[dict], **kwargs) -> dict:
        system, api_msgs = self._split_system(
            messages, kwargs.pop("system", self.config.get("system"))
        )
        max_uses = kwargs.pop("max_uses", 5)
        tools = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}
        ]
        params = {
            "model": self._require_model(),
            "max_tokens": self._max_tokens,
            "messages": api_msgs,
            "tools": tools,
        }
        params.update(kwargs)
        if system:
            params["system"] = system
        with translated_sdk_errors(self._sdk, "anthropic"):
            response = self._client.messages.create(**params)

        texts: list[str] = []
        sources: list[str] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                texts.append(block.text)
                for cit in getattr(block, "citations", None) or []:
                    url = getattr(cit, "url", None)
                    if url:
                        sources.append(url)
            elif btype == "web_search_tool_result":
                for r in getattr(block, "content", None) or []:
                    url = getattr(r, "url", None)
                    if url:
                        sources.append(url)
        return {
            "text": "\n".join(t for t in texts if t).strip(),
            "sources": list(dict.fromkeys(sources)),  # dedupe, keep order
        }
