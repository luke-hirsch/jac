from abc import ABC, abstractmethod
from collections.abc import Generator


class LLMTransportError(RuntimeError):
    """The provider host could not be reached (connection refused, DNS, timeout).

    Distinct from an HTTP error response: nothing reached the model, so the call is
    safe to retry once. A RuntimeError subclass so existing broad handlers keep working.
    """


class LLMAdapter(ABC):
    """Interface every provider adapter must implement.

    Adapters receive the resolved config dict (provider, model, api_key, url,
    max_tokens, any extras) and expose complete() / stream(). The client
    layer handles logging and user resolution; adapters focus on the HTTP call.
    """

    supports_web_search: bool = False

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

    def embed(self, inputs: list[str]) -> list[list[float]]:
        """Return an embedding vector for each input string.

        Optional capability — only providers with an embedding endpoint
        implement it (today: the native Ollama adapter). Raises
        NotImplementedError otherwise.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support embeddings.")

    def web_search(self, messages: list[dict], **kwargs) -> dict:
        """Run a completion with provider-native web search.

        Optional capability — only providers with supports_web_search = True implement it.
        Returns {"text": str, "sources": [str]}. Raises NotImplementedError otherwise (mirrors
        embed()); the flag is the clean pre-check, this is the backstop.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support web search.")

    def map_params(self, params: dict) -> dict:
        """Translate generic per-run knobs into this provider's native kwargs.
        Base: no knobs — unknown-provider safety (ollama must NEVER see knob
        kwargs; its payload builder forwards every kwarg onto the wire)."""
        return {}
