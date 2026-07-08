import contextvars
import time
from collections.abc import Generator
from contextlib import contextmanager

from .base import LLMTransportError
from .conf import get_alias_config, logging_enabled
from .registry import get_adapter_class

# One retry for connection-level failures (host unreachable / timeout) — enough to ride
# out a model server that is briefly busy, without double-billing HTTP-level errors.
RETRY_DELAY_S = 2.0

# Lets a caller observe retries without threading a callback through every prompt helper:
# the generation task sets a reporter for its own execution context and surfaces
# "timed out, retrying in Ns" to its progress channel.
_retry_reporter: contextvars.ContextVar = contextvars.ContextVar(
    "llm_retry_reporter", default=None
)


@contextmanager
def retry_reporter(callback):
    """Scope a `callback(operation: str, delay_s: float, error: str)` to this context;
    it fires once per transport-level retry made by any LLMClient inside the block."""
    token = _retry_reporter.set(callback)
    try:
        yield
    finally:
        _retry_reporter.reset(token)


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

    def _with_retry(self, operation: str, call):
        """Run an adapter call, retrying once on a transport failure. Notifies the
        context's retry reporter (if any) before sleeping, so long-running callers
        can tell their user what is happening instead of silently stalling."""
        try:
            return call()
        except LLMTransportError as exc:
            reporter = _retry_reporter.get()
            if reporter is not None:
                try:
                    reporter(operation, RETRY_DELAY_S, str(exc))
                except Exception:  # noqa: BLE001 — a broken reporter must not kill the call
                    pass
            time.sleep(RETRY_DELAY_S)
            return call()

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
            response_text = self._with_retry(
                "completion", lambda: self._adapter.complete(msgs, **kwargs)
            )
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
        return self._with_retry("embedding", lambda: self._adapter.embed(inputs))

    def web_search(
        self, prompt: str | None = None, *, messages: list[dict] | None = None, **kwargs
    ) -> dict:
        """Run a web-search-backed completion. Returns {"text": str, "sources": [str]}."""
        msgs = _normalise_messages(prompt, messages)
        start = time.monotonic()
        error_text = ""
        result: dict = {"text": "", "sources": []}
        try:
            result = self._adapter.web_search(msgs, **kwargs)
            return result
        except Exception as exc:
            error_text = str(exc)
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            if logging_enabled():
                self._write_log(
                    msgs, result.get("text", ""), error_text, None, None, latency_ms
                )

    @property
    def supports_web_search(self) -> bool:
        return getattr(self._adapter, "supports_web_search", False)
