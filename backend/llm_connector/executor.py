"""The Executor value object: WHO runs a pipeline rung.

(provider, model, user), frozen. The jac pipeline threads exactly one of these
through a whole generation run — the single-executor invariant lives here. `model`
None means "the executor's own default" (HirschAI's row model / the catalog
default). Embedding is deliberately NOT on this class: it is a tower-only
capability (llm_connector.embed()), and a commercial executor must never grow one.
"""

from dataclasses import dataclass

from .client import LLMClient
from .conf import HIRSCHAI_PROVIDER
from .registry import get_adapter_class


@dataclass(frozen=True)
class Executor:
    provider: str
    model: str | None = None
    user: object = None
    params: dict | None = None

    def _client(self) -> LLMClient:
        return LLMClient(self.provider, user=self.user, model=self.model)

    def complete(self, prompt=None, *, messages=None, **kwargs) -> str:
        if self.params:
            kwargs.setdefault("params", self.params)
        return self._client().complete(prompt=prompt, messages=messages, **kwargs)

    def stream(self, prompt=None, *, messages=None, **kwargs):
        return self._client().stream(prompt=prompt, messages=messages, **kwargs)

    def web_search(self, prompt=None, *, messages=None, **kwargs) -> dict:
        return self._client().web_search(prompt=prompt, messages=messages, **kwargs)

    @property
    def supports_web_search(self) -> bool:
        try:
            cls = get_adapter_class(self.provider)
        except Exception:  # noqa: BLE001 — unknown provider can't search
            return False
        return bool(getattr(cls, "supports_web_search", False))

    @property
    def is_hirschai(self) -> bool:
        return self.provider == HIRSCHAI_PROVIDER
