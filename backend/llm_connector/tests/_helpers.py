"""Shared fixtures for the llm_connector test package.

Not a `test*.py` module, so the runner never collects it. Importing it once
registers the in-memory ``fake`` provider used across the client tests.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from llm_connector.base import LLMAdapter
from llm_connector.registry import register


@contextmanager
def _muted():
    """Silence logging inside the block. Wrap ONLY the tests that deliberately
    exercise the no-config fallback path (which logs an expected 'falling back
    to settings' warning). Logging anywhere else still surfaces — so an
    unexpected line in the run output always means something is genuinely off."""
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
        self.complete_calls: list[tuple[list[dict], dict]] = []
        self.stream_calls: list[tuple[list[dict], dict]] = []
        self.response = config.get("_response", "pong")
        self.chunks = config.get("_chunks", ["pi", "ng"])
        self.raise_on_complete: Exception | None = config.get("_raise")
        FakeAdapter.instances.append(self)

    def complete(self, messages: list[dict], **kwargs) -> str:
        self.complete_calls.append((messages, kwargs))
        if self.raise_on_complete:
            raise self.raise_on_complete
        return self.response

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        self.stream_calls.append((messages, kwargs))
        for chunk in self.chunks:
            yield chunk


register("fake")(FakeAdapter)


FAKE_LLM = {
    "default": {"provider": "fake", "model": "fake-1"},
    "other": {"provider": "fake", "model": "fake-2", "_response": "hello"},
}
