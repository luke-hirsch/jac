from collections.abc import Generator

from django.core.exceptions import ImproperlyConfigured

from ..base import LLMAdapter
from ..registry import register


@register("anthropic")
class AnthropicAdapter(LLMAdapter):
    """Adapter for the Anthropic Messages API.

    Requires the `anthropic` package and an api_key in the config.
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
        self._client = _anthropic.Anthropic(api_key=api_key)
        self._model = config.get("model", "claude-sonnet-4-6")
        self._max_tokens = config.get("max_tokens", 4096)

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
        params = dict(
            model=self._model, max_tokens=self._max_tokens, messages=api_msgs, **kwargs
        )
        if system:
            params["system"] = system
        response = self._client.messages.create(**params)
        return response.content[0].text

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        system, api_msgs = self._split_system(
            messages, kwargs.pop("system", self.config.get("system"))
        )
        params = dict(
            model=self._model, max_tokens=self._max_tokens, messages=api_msgs, **kwargs
        )
        if system:
            params["system"] = system
        with self._client.messages.stream(**params) as s:
            for text in s.text_stream:
                yield text

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
        params = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=api_msgs,
            tools=tools,
            **kwargs,
        )
        if system:
            params["system"] = system
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
