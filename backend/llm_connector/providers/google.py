from collections.abc import Generator

from django.core.exceptions import ImproperlyConfigured

from ..base import LLMAdapter
from ..registry import register


def _to_google_messages(messages: list[dict]) -> tuple[list, str | None]:
    """Convert the standard messages list to the Google GenerativeAI format.

    Splits out the system message (Google takes it as a constructor param),
    and maps role "assistant" → "model" which the SDK requires.
    Returns (history, system_instruction).
    """
    system = None
    history = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            system = content
        else:
            history.append(
                {"role": "model" if role == "assistant" else role, "parts": [content]}
            )
    return history, system


@register("google")
class GoogleAdapter(LLMAdapter):
    """Adapter for the Google Generative AI (Gemini) API.

    Requires the `google-generativeai` package and an api_key in the config.
    Each call creates a short-lived GenerativeModel so the system instruction
    and generation config can be set per request.
    """

    supports_web_search = True

    def __init__(self, config: dict):
        super().__init__(config)
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImproperlyConfigured(
                "The 'google-generativeai' package is required for the google provider. "
                "Run: pip install google-generativeai"
            )
        api_key = config.get("api_key")
        if not api_key:
            raise ImproperlyConfigured(
                "LLM google provider requires an 'api_key' in its config."
            )
        genai.configure(api_key=api_key)
        self._genai = genai
        self._model_name = config.get("model", "gemini-1.5-pro")
        self._max_tokens = config.get("max_tokens")

    def complete(self, messages: list[dict], **kwargs) -> str:
        history, system = _to_google_messages(messages)
        model = self._make_model(system)
        last = history.pop()
        chat = model.start_chat(history=history)
        response = chat.send_message(last["parts"][0], **kwargs)
        return response.text

    def stream(self, messages: list[dict], **kwargs) -> Generator[str, None, None]:
        history, system = _to_google_messages(messages)
        model = self._make_model(system)
        last = history.pop()
        chat = model.start_chat(history=history)
        for chunk in chat.send_message(last["parts"][0], stream=True, **kwargs):
            if chunk.text:
                yield chunk.text

    def _make_model(self, system: str | None, tools=None):
        """Construct a GenerativeModel with optional system instruction, token cap, and tools."""
        kwargs = {"model_name": self._model_name}
        if system:
            kwargs["system_instruction"] = system
        if self._max_tokens:
            import google.generativeai.types as gtypes

            kwargs["generation_config"] = gtypes.GenerationConfig(
                max_output_tokens=self._max_tokens
            )
        if tools is not None:
            kwargs["tools"] = tools
        return self._genai.GenerativeModel(**kwargs)

    # Gemini 2.x grounding tool. (Gemini 1.5 uses the dynamic-retrieval variant
    # "google_search_retrieval"; override via the `search_tool` config key if needed.)
    def web_search(self, messages: list[dict], **kwargs) -> dict:
        """Run a completion grounded with Google Search (Gemini). Returns {"text", "sources"}.

        Uses the legacy google-generativeai SDK to match complete(); sources come from each
        candidate's grounding_metadata.grounding_chunks[].web.uri.
        """
        kwargs.pop("max_uses", None)  # Anthropic-only search cap; not a Gemini param
        tool = self.config.get("search_tool", "google_search")
        history, system = _to_google_messages(messages)
        model = self._make_model(system, tools=tool)
        last = history.pop()
        chat = model.start_chat(history=history)
        response = chat.send_message(last["parts"][0], **kwargs)

        sources: list[str] = []
        for cand in getattr(response, "candidates", None) or []:
            meta = getattr(cand, "grounding_metadata", None)
            for chunk in getattr(meta, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None)
                if uri:
                    sources.append(uri)
        return {
            "text": (getattr(response, "text", "") or "").strip(),
            "sources": list(dict.fromkeys(sources)),  # dedupe, keep order
        }
