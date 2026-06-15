"""Native Ollama adapter (the /api/chat endpoint).

Distinct from the `custom` provider, which speaks the OpenAI-compatible
/v1/chat/completions wire format. The /v1 endpoint silently ignores
`think: false` for qwen3-style reasoning models — they then burn their whole
generation budget thinking and time out. Ollama's native /api/chat honours
`think: false` at the top level, so reasoning can actually be turned off.

Wire differences from the OpenAI shape this adapter handles:
  - generation params live in an `options` object (num_predict, temperature, …),
    so `max_tokens` is mapped to `options.num_predict`;
  - `think` and `keep_alive` are top-level fields;
  - the response is `{"message": {"content": ...}}`, and streaming is
    newline-delimited JSON objects, not SSE `data:` lines.
  - embeddings use /api/embed with a separate `embed_model`.

Uses stdlib urllib so it has no dependency on the `openai` package.
"""

import json
from collections.abc import Generator
from urllib import error, request

from django.core.exceptions import ImproperlyConfigured

from ..base import LLMAdapter
from ..registry import register

_KNOWN = {
    "provider",
    "url",
    "model",
    "api_key",
    "timeout",
    "strength",  # pipeline-only capability hint; not an API param
    "max_tokens",
    "think",
    "keep_alive",
    "options",
    "embed_model",  # consumed by embed(), not a chat-payload param
}


@register("ollama")
class OllamaAdapter(LLMAdapter):
    """Adapter for Ollama's native /api/chat endpoint, with real `think` support."""

    def __init__(self, config: dict):
        super().__init__(config)
        url = config.get("url")
        if not url:
            raise ImproperlyConfigured(
                "LLM ollama provider requires a 'url' in its config "
                "(e.g. 'http://localhost:11434')."
            )
        # Accept a base url with or without a trailing /v1 (the OpenAI-compat
        # path) so the same OLLAMA_URL works for both providers.
        base = url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")].rstrip("/")
        self._endpoint = base + "/api/chat"
        self._embed_endpoint = base + "/api/embed"
        self._api_key = config.get("api_key")
        self._model = config.get("model", "llama3")
        # Embeddings use a separate model (e.g. qwen3-embedding:0.6b), not the chat model.
        self._embed_model = config.get("embed_model")
        self._timeout = config.get("timeout", 120)
        self._think = config.get("think")  # None | bool — only sent when set
        self._keep_alive = config.get("keep_alive")
        options = dict(config.get("options") or {})
        if config.get("max_tokens"):
            options.setdefault("num_predict", config["max_tokens"])
        self._options = options
        self._extra = {k: v for k, v in config.items() if k not in _KNOWN}

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(self, messages: list[dict], stream: bool, **kwargs) -> dict:
        payload: dict = {"model": self._model, "messages": messages, "stream": stream}
        if self._think is not None:
            payload["think"] = self._think
        if self._keep_alive is not None:
            payload["keep_alive"] = self._keep_alive
        options = dict(self._options)
        options.update(kwargs.pop("options", None) or {})
        if options:
            payload["options"] = options
        payload.update(self._extra)
        payload.update(kwargs)  # per-call overrides take precedence
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
                f"LLM ollama provider HTTP {exc.code} at {self._endpoint}: {body}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(
                f"LLM ollama provider could not reach {self._endpoint}: {exc.reason}"
            ) from exc

    def complete(self, messages: list[dict], **kwargs) -> str:
        with self._request(self._payload(messages, stream=False, **kwargs)) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return (body.get("message") or {}).get("content", "") or ""

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        with self._request(self._payload(messages, stream=True, **kwargs)) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (chunk.get("message") or {}).get("content")
                if content:
                    yield content
                if chunk.get("done"):
                    break

    def embed(self, inputs: list[str]) -> list[list[float]]:
        """Embed each input string via /api/embed. Uses `embed_model` (falls back
        to the chat model). Batches all inputs in one request."""
        if not inputs:
            return []
        payload = {"model": self._embed_model or self._model, "input": inputs}
        req = request.Request(
            self._embed_endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"LLM ollama provider HTTP {exc.code} at {self._embed_endpoint}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(
                f"LLM ollama provider could not reach {self._embed_endpoint}: {exc.reason}"
            ) from exc
        return body.get("embeddings") or []
