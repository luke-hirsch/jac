import contextvars
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings

from .base import LLMTransportError
from .conf import HIRSCHAI_PROVIDER, log_bodies, logging_enabled, resolve_config
from .probe import note_hirschai_answered, note_hirschai_failed
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


# Between two attempts while the model server is gone: quick at first, then settling down.
_COMPUTE_BACKOFF_S = (5.0, 10.0, 20.0, 30.0)


@dataclass
class _ComputeWait:
    """One worker's budget for a model server that isn't there yet. The clock starts at the first
    failure and stops at the next success, so a long pipeline never eats into it."""

    budget_s: float
    on_wait: object = None  # callback(delay_s: float, error: str)
    on_resume: object = None  # callback()
    deadline: float | None = None
    attempt: int = 0

    def run(self, call):
        while True:
            try:
                result = call()
            except LLMTransportError as exc:
                self._pause(exc)
                continue
            if self.deadline is not None:
                self.deadline = None
                self.attempt = 0
                if self.on_resume is not None:
                    self.on_resume()
            return result

    def _pause(self, exc: LLMTransportError) -> None:
        now = time.monotonic()
        if self.deadline is None:
            self.deadline = now + self.budget_s
        remaining = self.deadline - now
        if remaining <= 0:
            raise exc
        step = _COMPUTE_BACKOFF_S[min(self.attempt, len(_COMPUTE_BACKOFF_S) - 1)]
        delay = min(getattr(exc, "retry_after", step) or step, remaining)
        self.attempt += 1
        if self.on_wait is not None:
            try:
                self.on_wait(delay, str(exc))
            except Exception:  # noqa: BLE001, S110 — a broken reporter must not kill the call
                pass
        time.sleep(delay)


_compute_wait: contextvars.ContextVar = contextvars.ContextVar(
    "llm_compute_wait", default=None
)
_interactive_timeout: contextvars.ContextVar = contextvars.ContextVar(
    "llm_interactive_timeout", default=None
)


@contextmanager
def wait_for_compute(*, on_wait=None, on_resume=None, budget_s: float | None = None):
    """Worker scope: the job has no one waiting at the door, so it can ride out a model server
    that is off, starting or restarting. HirschAI calls in the block retry within the budget; any
    `503 + Retry-After` sets the pace. A commercial API is not "waking up": it keeps its one retry.
    Past the budget the transport error propagates and the job fails with a message."""
    budget = settings.LLM_COMPUTE_WAIT_S if budget_s is None else budget_s
    token = _compute_wait.set(
        _ComputeWait(budget_s=budget, on_wait=on_wait, on_resume=on_resume)
    )
    try:
        yield
    finally:
        _compute_wait.reset(token)


@contextmanager
def interactive(timeout_s: float):
    """Request scope: somebody is watching a spinner and the caller has a path without AI. Short
    timeout, no retry, and the failure comes straight back so the page can move on."""
    token = _interactive_timeout.set(timeout_s)
    try:
        yield
    finally:
        _interactive_timeout.reset(token)


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

    def __init__(
        self,
        provider: str | None = None,
        *,
        user=None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        """provider None -> the user's default executor (their default commercial
        row, else HirschAI). `model` overrides the resolved config's model; `timeout`
        (or an `interactive` scope around the call) the HTTP timeout."""
        self.user = user
        self._config = resolve_config(provider, user=user, model=model)
        self.provider = self._config["provider"]
        timeout = _interactive_timeout.get() if timeout is None else timeout
        if timeout is not None:
            self._config["timeout"] = timeout
        self.model = self._config.get("model", "")
        adapter_cls = get_adapter_class(self.provider)
        self._adapter = adapter_cls(self._config)

    def _with_retry(self, operation: str, call):
        """Run an adapter call under the context's policy. Default: retry once on a transport
        failure, notifying the context's retry reporter (if any) before sleeping, so long-running
        callers can tell their user what is happening instead of silently stalling. See
        `wait_for_compute` and `interactive` for the two scopes that replace it."""
        wait = _compute_wait.get()
        try:
            if _interactive_timeout.get() is not None:
                result = call()
            elif wait is not None and self.provider == HIRSCHAI_PROVIDER:
                result = wait.run(call)
            else:
                result = self._retry_once(operation, call)
        except LLMTransportError:
            if self.provider == HIRSCHAI_PROVIDER:
                note_hirschai_failed()
            raise
        if self.provider == HIRSCHAI_PROVIDER:
            note_hirschai_answered()
        return result

    def _retry_once(self, operation: str, call):
        try:
            return call()
        except LLMTransportError as exc:
            reporter = _retry_reporter.get()
            if reporter is not None:
                try:
                    reporter(operation, RETRY_DELAY_S, str(exc))
                except Exception:  # noqa: BLE001, S110 — a broken reporter must not kill the call
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
        params = kwargs.pop("params", None)
        if params:
            kwargs.update(self._adapter.map_params(params))
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
        params = kwargs.pop("params", None)
        if params:
            kwargs.update(self._adapter.map_params(params))

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
        """Persist an LLMRequestLog row. Silently swallowed on any failure. Without
        LLM_LOG_BODIES the row is metadata only — no prompt, no response text."""
        try:
            from .models import LLMRequestLog

            bodies = log_bodies()
            LLMRequestLog.objects.create(
                user=self.user,
                provider=self._config["provider"],
                model=self._config.get("model", ""),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                request_messages=messages if bodies else [],
                response_text=response_text if bodies else "",
                error=error,
            )
        except Exception:  # noqa: BLE001, S110 — logging must never break a run
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
        params = kwargs.pop("params", None)
        if params:
            kwargs.update(self._adapter.map_params(params))
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
