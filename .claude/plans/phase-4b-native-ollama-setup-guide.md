# Phase 4b — Guide A: native Ollama provider (think-off + embeddings)

> Code-bearing setup guide. First of two guides for the Phase-4b
> realignment; [Guide B](phase-4b-bis-setup-guide.md) (the jac 3-tier strategy) builds on it.
> Both land as **one commit**. Claude has already run the diagnostics that justify every
> choice here (see §1); the testing in §Verify is yours to run.

## 1. Goal

Make the local default model usable, and add the embedding capability the new filter
strategy needs. Two things the existing `custom` (`/v1`) adapter can't do:

1. **Embeddings** via `/api/embed` (`qwen3-embedding:0.6b`) — the metric Guide B's filter
   rung uses. This is the immediate reason the native provider is needed (the `/v1` adapter
   has no clean embed path here).
2. **Disable qwen3-style reasoning** with a real `think:false`. `/v1/chat/completions`
   silently ignores it — a qwen3 model then reasons until the 300s timeout and returns empty
   content; native `/api/chat` honours it. The chosen default chat model, `llama3.2:1b`, is
   **non-thinking** (so `think:false` is harmlessly ignored), but keeping the flag future-proofs
   any qwen3-class model a user hooks up. (Original diagnostic on qwen3.5:0.8b: empty 200-token
   reasoning dump on `/v1`; clean 21-token answer on `/api/chat` with `think:false`.)

Plus **overridable strength auto-detection** so any model gets a sensible pipeline ladder
without hand-tagging. Does **not** touch the CV pipeline (that's Guide B).

## 2. Preflight

- On clean `b962da4` (`git log --oneline -1` → `cv tool removed json entirely`),
  `python manage.py test jac llm_connector` → `Ran 190 tests … OK`.
- Local Ollama at `$OLLAMA_URL` serving **`llama3.2:1b`** (chat) + **`qwen3-embedding:0.6b`** (embed).
- Confirm both answer:
  ```bash
  curl -s localhost:11434/api/chat -d '{"model":"llama3.2:1b","messages":[{"role":"user","content":"List 3 fruits."}],"think":false,"stream":false}' | python3 -c "import sys,json;print(repr(json.load(sys.stdin)['message']['content'])[:80])"
  curl -s localhost:11434/api/embed -d '{"model":"qwen3-embedding:0.6b","input":["hello"]}' | python3 -c "import sys,json;print('dim', len(json.load(sys.stdin)['embeddings'][0]))"   # -> dim 1024
  ```
  (The `/v1` `think:false` bug that motivated this provider was observed on qwen3.5:0.8b; it
  isn't reproducible now since no qwen3-class chat model is installed — `llama3.2:1b` is non-thinking.)

## 3. Stack additions

None — stdlib `urllib`, like the `custom` adapter.

## 4. The code

### 4a. `backend/llm_connector/providers/ollama.py` (new file)

```python
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

# Config keys the adapter consumes directly; everything else is forwarded
# verbatim at the top level of the request (rare — most extras belong in options).
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
```

### 4b. `backend/llm_connector/base.py` — add an optional `embed()`

After the abstract `stream` method, add:

```python
    def embed(self, inputs: list[str]) -> list[list[float]]:
        """Return an embedding vector for each input string.

        Optional capability — only providers with an embedding endpoint
        implement it (today: the native Ollama adapter). Raises
        NotImplementedError otherwise.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support embeddings."
        )
```

### 4c. `backend/llm_connector/client.py` — `LLMClient.embed()`

Add (e.g. just before `_write_log`):

```python
    def embed(self, inputs: list[str]) -> list[list[float]]:
        """Return an embedding vector per input string via the provider adapter.

        Not logged: embeddings are high-volume and carry no completion text worth
        auditing. Raises NotImplementedError for providers without an embed endpoint.
        """
        return self._adapter.embed(inputs)
```

### 4d. `backend/llm_connector/__init__.py` — module-level `embed()`

Append after `stream`:

```python
def embed(
    inputs: list[str],
    *,
    alias: str = "default",
    user=None,
) -> list[list[float]]:
    """Return an embedding vector per input string for the given alias.

    Only providers with an embedding endpoint support this (today: Ollama).
    """
    return get_client(alias, user=user).embed(inputs)
```

### 4e. `backend/llm_connector/registry.py` — register the provider

In `_load_builtin`'s `builtin` dict add:

```python
        "ollama": "llm_connector.providers.ollama",
```

### 4f. `backend/llm_connector/models.py` — `Provider` choice + migration

In `class Provider(models.TextChoices)` add:

```python
        ollama = "ollama", "Ollama (native /api/chat)"
```

Then `python manage.py makemigrations llm_connector` → `0004_alter_llmconfig_provider`.

### 4g. `backend/llm_connector/conf.py` — overridable auto-detect

Add `import re` at the top. Replace the `_STRENGTHS` line + `get_alias_strength` with:

```python
_STRENGTHS = {"light", "standard", "strong"}
# Parameter-size token in a model id, e.g. "0.8b" in "qwen3.5:0.8b" or "70b".
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b")
# Cloud model names without a size token that still signal a small/fast tier.
_SMALL_NAME_HINTS = ("haiku", "mini", "nano", "lite", "flash")


def _autodetect_strength(provider: str, model: str) -> str:
    """Best-effort capability guess from a model id, for aliases with no explicit
    `strength`. Conservative: anything we can't positively identify as small ->
    'strong' (the full ladder), so paid configs and existing tests keep their
    behaviour. Only models we recognise as small get opted down.
    """
    name = (model or "").lower()
    sizes = [float(m) for m in _SIZE_RE.findall(name)]
    if sizes:
        size = max(sizes)
        if size <= 3:
            return "light"
        if size <= 14:
            return "standard"
        return "strong"
    if any(hint in name for hint in _SMALL_NAME_HINTS):
        return "standard"
    return "strong"


def get_alias_strength(alias: str = "default", user=None) -> str:
    """Pipeline capability hint for an alias: 'light' | 'standard' | 'strong'.

    An explicit, valid `strength` in the resolved config (LLMConfig.extra for
    per-user rows, the settings.LLM dict for the default) always wins. Otherwise
    auto-detect from the model id. Unknown -> 'strong' (full ladder), preserving
    prior behaviour for anything we don't recognise.
    """
    try:
        config = get_alias_config(alias, user=user)
    except Exception:  # noqa: BLE001 — missing/broken config -> safe default
        return "strong"
    strength = config.get("strength")
    if strength in _STRENGTHS:
        return strength
    return _autodetect_strength(config.get("provider", ""), config.get("model", ""))
```

### 4h. `backend/lukehirsch/settings.py` — native default + embed model

Replace the `LLM = {...}` block with:

```python
LLM = {
    "default": {
        # Native Ollama /api/chat — NOT the OpenAI-compat /v1 endpoint, which
        # silently ignores `think: false` for qwen3 models (they reason to the
        # timeout wall). The native endpoint honours it (see providers/ollama.py).
        "provider": "ollama",
        "url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
        "model": "llama3.2:1b",  # small, non-thinking; the cover-letter writer (Phase 4c)
        # Embedding model for the filter rung's deterministic ranking (1024-dim).
        # Small enough to share the server's RAM with the chat model.
        "embed_model": "qwen3-embedding:0.6b",
        "timeout": 300,  # SLMs are slow; the old 120 timed out mid-generation
        "think": False,  # harmless for llama3.2; suppresses reasoning for any qwen3 a user adds
        # Pipeline capability hint (consumed by get_alias_strength, stripped from
        # the HTTP payload by the adapter); llama3.2:1b also auto-detects to "light".
        "strength": "light",
    },
}
```

### 4i. `backend/llm_connector/tests.py` — type these (then run them)

Add `import json` at the top if missing. Add the `OllamaAdapterTests` class (after
`OpenAIAdapterParamTests`) and the four autodetect tests inside `ConfTests`:

```python
class OllamaAdapterTests(TestCase):
    """Native /api/chat adapter — the key win over /v1 is honouring `think`."""

    def _adapter(self, **over):
        from llm_connector.providers.ollama import OllamaAdapter

        cfg = {"url": "http://localhost:11434/v1", "model": "qwen3.5:0.8b", **over}
        return OllamaAdapter(cfg)

    @staticmethod
    def _fake_resp(body):
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps(body).encode("utf-8")
        return resp

    def _capture_request(self, adapter, body, method="complete", **call_kwargs):
        import llm_connector.providers.ollama as mod

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return self._fake_resp(body)

        with patch.object(mod.request, "urlopen", fake_urlopen):
            out = getattr(adapter, method)(
                [{"role": "user", "content": "hi"}]
                if method == "complete"
                else ["a", "b"],
                **call_kwargs,
            )
        return out, captured

    def test_endpoint_strips_v1_and_targets_api_chat(self):
        adapter = self._adapter()
        out, cap = self._capture_request(adapter, {"message": {"content": "hello"}})
        self.assertTrue(cap["url"].endswith("/api/chat"))
        self.assertNotIn("/v1", cap["url"])
        self.assertEqual(out, "hello")

    def test_think_false_sent_top_level(self):
        adapter = self._adapter(think=False)
        _, cap = self._capture_request(adapter, {"message": {"content": "x"}})
        self.assertIs(cap["payload"]["think"], False)

    def test_think_omitted_when_unset(self):
        adapter = self._adapter()
        _, cap = self._capture_request(adapter, {"message": {"content": "x"}})
        self.assertNotIn("think", cap["payload"])

    def test_max_tokens_maps_to_options_num_predict(self):
        adapter = self._adapter(max_tokens=256)
        _, cap = self._capture_request(adapter, {"message": {"content": "x"}})
        self.assertEqual(cap["payload"]["options"]["num_predict"], 256)

    def test_missing_url_raises(self):
        from llm_connector.providers.ollama import OllamaAdapter

        with self.assertRaises(ImproperlyConfigured):
            OllamaAdapter({"model": "x"})

    def test_embed_uses_embed_model_and_endpoint(self):
        adapter = self._adapter(embed_model="qwen3-embedding:0.6b")
        out, cap = self._capture_request(
            adapter, {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}, method="embed"
        )
        self.assertTrue(cap["url"].endswith("/api/embed"))
        self.assertEqual(cap["payload"]["model"], "qwen3-embedding:0.6b")
        self.assertEqual(cap["payload"]["input"], ["a", "b"])
        self.assertEqual(out, [[0.1, 0.2], [0.3, 0.4]])

    def test_embed_empty_inputs_skips_request(self):
        adapter = self._adapter(embed_model="qwen3-embedding:0.6b")
        self.assertEqual(adapter.embed([]), [])

    def test_registered_as_ollama_provider(self):
        from llm_connector.providers.ollama import OllamaAdapter
        from llm_connector.registry import get_adapter_class

        self.assertIs(get_adapter_class("ollama"), OllamaAdapter)
```

Inside `ConfTests` (the existing `fake-1` autodetect tests stay green — `fake-1` is
unrecognised → strong):

```python
    @override_settings(LLM={"default": {"provider": "custom", "model": "qwen3.5:0.8b"}})
    def test_get_alias_strength_autodetects_small_local_as_light(self):
        self.assertEqual(get_alias_strength("default"), "light")

    @override_settings(LLM={"default": {"provider": "openai", "model": "gpt-4o-mini"}})
    def test_get_alias_strength_autodetects_mini_as_standard(self):
        self.assertEqual(get_alias_strength("default"), "standard")

    @override_settings(LLM={"default": {"provider": "anthropic", "model": "claude-opus-4-8"}})
    def test_get_alias_strength_autodetects_large_as_strong(self):
        self.assertEqual(get_alias_strength("default"), "strong")

    @override_settings(LLM={"default": {"provider": "custom", "model": "qwen3.5:0.8b", "strength": "strong"}})
    def test_get_alias_strength_explicit_overrides_autodetect(self):
        self.assertEqual(get_alias_strength("default"), "strong")
```

## Verify (run by Lukas)

- `python manage.py test llm_connector` → OK (adapter + autodetect tests pass).
- `python manage.py shell -c "from llm_connector import complete; print(repr(complete(prompt='List 5 fruits.')[:40]))"`
  → a real list, fast (not empty, not a 5-min hang).
- `python manage.py shell -c "from llm_connector import embed; print(len(embed(['a','b'])), 'x', len(embed(['a'])[0]))"`
  → `2 x 1024` (qwen3-embedding:0.6b).

## What you should have

```
backend/llm_connector/providers/ollama.py     # new
backend/llm_connector/base.py                  # embed() default
backend/llm_connector/client.py                # embed()
backend/llm_connector/__init__.py              # embed()
backend/llm_connector/registry.py              # ollama in _load_builtin
backend/llm_connector/models.py (+0004 migration)
backend/llm_connector/conf.py                  # _autodetect_strength + override
backend/lukehirsch/settings.py                 # native default + embed_model
backend/llm_connector/tests.py                 # OllamaAdapterTests + autodetect
```

Don't commit yet — Guide B lands in the same commit.

## Known gaps

- `/v1` can't disable qwen3 thinking at all (both `think` and `chat_template_kwargs.enable_thinking`
  were tested and ignored) — that's why this is a new provider, not a flag on `custom`. `custom`
  stays for genuine OpenAI-compat servers.
- Auto-detect is heuristic (model names churn); explicit `strength` is the override, unknown→strong stays safe.

## What's next

[Guide B](phase-4b-bis-setup-guide.md) — delete the tag-word filter, add the embedding rung,
and rewrite the ladder to the 3-tier strategy.
