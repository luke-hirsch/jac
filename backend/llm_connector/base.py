from abc import ABC, abstractmethod
from collections.abc import Generator


class LLMAdapter(ABC):
    """Interface every provider adapter must implement.

    Adapters receive the resolved config dict (provider, model, api_key, url,
    max_tokens, any extras) and expose complete() / stream(). The client
    layer handles logging and user resolution; adapters focus on the HTTP call.
    """

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def complete(self, messages: list[dict], **kwargs) -> str:
        """Send messages and return the full response text."""
        ...

    @abstractmethod
    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        """Send messages and yield response text chunks as they arrive."""
        ...

    def token_counts(self, response) -> tuple[int | None, int | None]:
        """Return (prompt_tokens, completion_tokens) from a provider response object.

        Returns (None, None) by default; providers that expose usage data override this.
        """
        return None, None
