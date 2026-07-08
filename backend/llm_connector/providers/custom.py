"""Generic OpenAI-compatible HTTP adapter.

Works with Ollama, LM Studio, vLLM, and any /v1/chat/completions endpoint.
Uses stdlib urllib so it has no dependency on the `openai` package.
"""
import json
from collections.abc import Generator
from urllib import error, request

from django.core.exceptions import ImproperlyConfigured

from ..base import LLMAdapter, LLMTransportError
from ..registry import register


@register("custom")
class CustomHTTPAdapter(LLMAdapter):
    """Adapter for any OpenAI-compatible /v1/chat/completions HTTP endpoint.

    Primarily used for local Ollama models (the zero-cost default), but works
    with any server that speaks the OpenAI chat completions wire format.
    Extra config keys beyond the recognised set are forwarded verbatim in the
    request payload — use this for provider-specific flags like `think: false`.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        url = config.get("url")
        if not url:
            raise ImproperlyConfigured(
                "LLM custom provider requires a 'url' in its config "
                "(e.g. 'http://localhost:11434/v1')."
            )
        self._endpoint = url.rstrip("/") + "/chat/completions"
        # Ollama and most compatible servers don't need a real key.
        self._api_key = config.get("api_key")
        self._model = config.get("model", "llama3")
        self._max_tokens = config.get("max_tokens")
        self._timeout = config.get("timeout", 120)
        # Any unrecognised config keys are forwarded verbatim to the API payload.
        # Use this for provider-specific flags, e.g. think: false for Qwen3 models.
        _known = {
            "provider",
            "url",
            "model",
            "api_key",
            "max_tokens",
            "timeout",
            "strength",  # pipeline-only capability hint; not an API param
        }
        self._extra_params = {k: v for k, v in config.items() if k not in _known}

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(self, messages: list[dict], stream: bool, **kwargs) -> dict:
        payload: dict = {"model": self._model, "messages": messages, "stream": stream}
        if self._max_tokens:
            payload["max_tokens"] = self._max_tokens
        payload.update(self._extra_params)  # config-level extras (e.g. think: false)
        payload.update(kwargs)              # per-call overrides take precedence
        return payload

    def _request(self, payload: dict):
        """Open an HTTP POST to the endpoint. Raises RuntimeError on HTTP/URL errors."""
        req = request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            return request.urlopen(req, timeout=self._timeout)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"LLM custom provider HTTP {exc.code} at {self._endpoint}: {body}"
            ) from exc
        except error.URLError as exc:
            raise LLMTransportError(
                f"LLM custom provider could not reach {self._endpoint}: {exc.reason}"
            ) from exc

    def complete(self, messages: list[dict], **kwargs) -> str:
        with self._request(self._payload(messages, stream=False, **kwargs)) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        with self._request(self._payload(messages, stream=True, **kwargs)) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
