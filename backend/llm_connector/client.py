import time
from collections.abc import Generator

from .conf import get_alias_config, logging_enabled
from .registry import get_adapter_class


def _normalise_messages(prompt: str | None, messages: list[dict] | None) -> list[dict]:
    """Coerce the two accepted input forms into a messages list.

    Raises ValueError when neither prompt nor messages is provided.
    """
    if messages is not None:
        return messages
    if prompt is not None:
        return [{"role": "user", "content": prompt}]
    raise ValueError("Provide either 'prompt' or 'messages'.")


class LLMClient:
    """Thin wrapper around a provider adapter that adds request logging.

    Resolves the correct adapter on construction via get_alias_config, so
    per-user LLMConfig rows are respected when `user` is provided.
    """

    def __init__(self, alias: str = "default", user=None):
        """Args:
        alias: LLM alias name to look up (e.g. "default", "reasoning").
        user: Django user instance or PK. When provided, per-user LLMConfig
            takes precedence over settings.LLM.
        """
        self.alias = alias
        self.user = user
        self._config = get_alias_config(alias, user=user)
        adapter_cls = get_adapter_class(self._config["provider"])
        self._adapter = adapter_cls(self._config)

    def complete(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict] | None = None,
        **kwargs,
    ) -> str:
        """Send a blocking completion request and return the full response text."""
        msgs = _normalise_messages(prompt, messages)
        start = time.monotonic()
        error_text = ""
        response_text = ""
        prompt_tokens = completion_tokens = None

        try:
            response_text = self._adapter.complete(msgs, **kwargs)
            return response_text
        except Exception as exc:
            error_text = str(exc)
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            if logging_enabled():
                self._write_log(
                    msgs,
                    response_text,
                    error_text,
                    prompt_tokens,
                    completion_tokens,
                    latency_ms,
                )

    def stream(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict] | None = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Yield response text chunks as they arrive from the provider."""
        msgs = _normalise_messages(prompt, messages)
        start = time.monotonic()
        error_text = ""
        collected: list[str] = []

        try:
            for chunk in self._adapter.stream(msgs, **kwargs):
                collected.append(chunk)
                yield chunk
        except Exception as exc:
            error_text = str(exc)
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            if logging_enabled():
                self._write_log(
                    msgs,
                    "".join(collected),
                    error_text,
                    None,
                    None,
                    latency_ms,
                )

    def _write_log(
        self,
        messages: list[dict],
        response_text: str,
        error: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        latency_ms: int,
    ):
        """Persist an LLMRequestLog row. Silently swallowed on any failure."""
        try:
            from .models import LLMRequestLog

            LLMRequestLog.objects.create(
                user=self.user,
                alias=self.alias,
                provider=self._config["provider"],
                model=self._config.get("model", ""),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                request_messages=messages,
                response_text=response_text,
                error=error,
            )
        except Exception:
            pass

    def embed(self, inputs: list[str]) -> list[list[float]]:
        """Return an embedding vector per input string via the provider adapter.

        Not logged: embeddings are high-volume and carry no completion text worth
        auditing. Raises NotImplementedError for providers without an embed endpoint.
        """
        return self._adapter.embed(inputs)
