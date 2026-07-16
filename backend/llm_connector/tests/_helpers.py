"""Shared fixtures for the llm_connector test package.

Not a `test*.py` module, so the runner never collects it. Importing it once
registers the in-memory ``fake`` / ``fakesearch`` providers used across the
connector (and jac pipeline) tests.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from llm_connector.base import LLMAdapter
from llm_connector.registry import register


@contextmanager
def _muted():
    """Silence logging inside the block. Wrap ONLY the tests that deliberately
    exercise an expected-warning path. Logging anywhere else still surfaces — so
    an unexpected line in the run output always means something is genuinely off."""
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


class FakeAdapter(LLMAdapter):
    """In-memory adapter used to exercise the client without hitting a real API."""

    instances: list["FakeAdapter"] = []

    def __init__(self, config: dict):
        super().__init__(config)
        self.config = dict(config)  # our own copy — the base may not keep one
        self.complete_calls: list[tuple[list[dict], dict]] = []
        self.stream_calls: list[tuple[list[dict], dict]] = []
        self.embed_calls: list[list[str]] = []
        self.response = config.get("_response", "pong")
        self.chunks = config.get("_chunks", ["pi", "ng"])
        self.raise_on_complete: Exception | None = config.get("_raise")
        # None = raise on every call (legacy semantics); an int = raise on the first
        # N calls only, then succeed — for exercising the client's transport retry.
        self.raise_times: int | None = config.get("_raise_times")
        self._raised = 0
        FakeAdapter.instances.append(self)

    def complete(self, messages: list[dict], **kwargs) -> str:
        self.complete_calls.append((messages, kwargs))
        if self.raise_on_complete and (
            self.raise_times is None or self._raised < self.raise_times
        ):
            self._raised += 1
            raise self.raise_on_complete
        return self.response

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        self.stream_calls.append((messages, kwargs))
        for chunk in self.chunks:
            yield chunk

    def embed(self, inputs: list[str]) -> list[list[float]]:
        """Deterministic vectors so cosine math is exercisable."""
        self.embed_calls.append(list(inputs))
        return [[1.0, 0.0, 0.0] for _ in inputs]


class SearchyFakeAdapter(FakeAdapter):
    """Fake with provider-native web search — stands in for anthropic/openai in
    personal-paragraph tests."""

    supports_web_search = True

    def __init__(self, config: dict):
        super().__init__(config)
        self.web_calls: list[tuple[list[dict], dict]] = []

    def web_search(self, messages: list[dict], **kwargs) -> dict:
        self.web_calls.append((messages, kwargs))
        return {
            "text": self.config.get("_dossier", "Acme builds delightful rockets."),
            "sources": ["https://example.com/about"],
        }


register("fake")(FakeAdapter)
register("fakesearch")(SearchyFakeAdapter)


# Seed dict for the system HirschAI row (mirrors settings.HIRSCHAI in dev).
TEST_HIRSCHAI = {
    "url": "http://localhost:11434",
    "model": "llama3.2:1b",
    "embed_model": "qwen3-embedding:0.6b",
    "think": False,
}


def fake_row(user, provider="fake", *, model="fake-1", default=False,
             api_key="sk-test", **extra):
    """An LLMConfig row whose executor resolves to the in-memory FakeAdapter.

    The new LLMConfig stores no model field — per-run models come from the
    catalog — so fake rows carry theirs in `extra`, exactly like the HirschAI
    row does. Extra kwargs must be JSON-serialisable (exception objects for the
    retry tests go through test_client's `client_for` instead).
    """
    from llm_connector.models import LLMConfig

    row = LLMConfig(
        user=user, provider=provider, default=default, extra={"model": model, **extra}
    )
    if api_key:
        row.api_key = api_key
    row.save()
    return row
