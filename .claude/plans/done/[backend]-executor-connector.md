# [backend] executor-connector

> **Rework guide 1 of 3** — *single-executor redesign (2026-07-16)*. Supersedes the alias/pin
> half of `[backend]-selection-ladder-remap` and all of
> `[backend]-llm-reachability-and-executors-endpoint` (both removed from `to-do/`; git history
> keeps them). Lands first; guides 2 (`pipeline-single-executor`) and 3 (`entry-pins`) build on
> it. One branch for all three — suggest renaming the current one:
> `git branch -m backend/executor-rework`.

## Context / goal

**The core invariant, decided 2026-07-16: a generation run touches exactly one executor.** An
executor is either **HirschAI** (the tower's Ollama, owned by the system user) or a **commercial
provider** (Anthropic / OpenAI) on the user's own key. No rung of a run ever routes elsewhere —
that one sentence replaces aliases, pins, `free_only` cost guards, and per-rung routing.

This guide makes the connector speak that language:

- `LLMConfig` = **user + provider + api_key + default flag**, unique per `(user, provider)`.
  No `alias`, no stored `model`. (Lukas already reshaped the model — this guide finishes the
  ecosystem around it.)
- The tower is a **system-owned row** (same `SystemScopedManager` pattern as `Domain` /
  `ApplicationLayout`), seeded from `settings.HIRSCHAI`, editable in the admin.
- The **model is a per-run pick** from a curated backend **catalog** (one dict Lukas maintains)
  served by a small endpoint; the backend validates against it.
- A tiny **reachability probe** answers "is HirschAI up" (drives the executors endpoint, jac's
  auto-run, and the live prompt tests' skip).
- A frozen **`Executor`** value object (`provider`, `model`, `user`) is what the jac pipeline
  threads through instead of `(alias, user)` pairs — guide 2 consumes it.

Privacy story the code must uphold: **commercial runs never touch the tower** (no data to
HirschAI), and embedding is a tower-only capability (`embed()` always resolves the system row —
commercial pipelines simply don't embed, see guide 2).

## Affected files

| Path | Change |
| --- | --- |
| `llm_connector/models.py` | `LLMConfig`: `SystemScopedManager`, single-default-per-user on `save()`; `LLMRequestLog` drops `alias`. |
| `llm_connector/conf.py` | **Rewrite**: `HIRSCHAI_PROVIDER`, `hirschai_row()`, `resolve_config()`, `resolve_executor()`/`ExecutorError`, `default_executor()`, `get_embed_floors()`. Everything alias/pin/free-only dies. |
| `llm_connector/catalog.py` | **New**: the curated model catalog + helpers. |
| `llm_connector/probe.py` | **New**: cached HirschAI reachability probe. |
| `llm_connector/executor.py` | **New**: the `Executor` dataclass. |
| `llm_connector/client.py` | `LLMClient(provider=None, *, user, model)`; log rows carry provider/model, no alias. |
| `llm_connector/__init__.py` | Public helpers rekeyed (`provider`/`model`); `embed()` is tower-only; `web_search`/`can_web_search` module helpers die (use `Executor`). |
| `llm_connector/serializers.py` | `LLMConfigSerializer` → id/provider/default/api_key/has_api_key; rejects `ollama`; SSRF url validation leaves the API (no url field). |
| `llm_connector/views.py` | `LLMPinView` + `LLMAliasListView` die; **`ExecutorListView`** replaces them; `check` action rekeyed. |
| `llm_connector/urls.py` | `aliases/`+`pins/` → `executors/`. |
| `llm_connector/admin.py` | Fix `list_display` (stale `model` field, stray `"def"`), drop alias filters. |
| `llm_connector/management/commands/llm_check.py` | Probe + per-provider round-trip, no aliases. |
| `lukehirsch/settings.py` | `LLM` dict → `HIRSCHAI` seed dict; `LLM_LOGGING` and the SSRF allowlist stay. |
| `backend/*/migrations/` | Already deleted — fresh `makemigrations llm_connector jac spa` + `migrate` at the end of guide 2. |

## Approach / key decisions

- **The system row is the runtime truth for the tower; `settings.HIRSCHAI` only seeds it.**
  `hirschai_row()` get_or_creates `(system user, provider=ollama)` from the settings dict, so a
  fresh DB self-heals and the admin can retune url/models without a deploy. `settings.LLM` and
  the whole alias-resolution stack die.
- **Users cannot own ollama rows.** The API serializer rejects `provider=ollama`; the tower is
  the operator's. (The admin can still create arbitrary rows — operator tooling.)
- **`default` means "my runs go to this commercial provider by default".** Exclusivity is
  enforced in `LLMConfig.save()` (last write wins), not in the serializer, so the admin obeys the
  same rule. A default row without a stored key does not count — `default_executor()` skips it
  and falls through to HirschAI-if-reachable, else `None` (jac's "manual only" state).
- **Model choice is per-run, validated against the catalog.** The catalog is one Python dict —
  Lukas maintains it, the endpoint serves it, `resolve_executor` validates against it. Each
  provider names exactly one `default: True` model (what auto-runs use). HirschAI's models are
  not in the catalog — they live on the system row (`extra.model` / `extra.embed_model`) and a
  run's `model` field stays blank for it.
- **`Executor` is a value object, not a session.** Frozen dataclass; each `complete()` resolves
  config fresh (same cost profile as the old per-call `complete(alias=…)`). `supports_web_search`
  reads the adapter class flag without instantiating. Guide 2 threads it through every rung.
- **The executors endpoint is the generate panel's single source.** One GET returns HirschAI
  (with live `reachable`) + every catalog provider (with `configured`, `default`, `models`,
  `modes`). The `modes` strings (`standard`/`high`) are jac vocabulary served as opaque labels —
  pragmatic denormalisation so the SPA needs one request, noted here so nobody "fixes" it into a
  second endpoint.
- **Request logs identify calls by provider+model.** The alias column dies; nothing else changes
  about the spend audit.

## The code

### 1. `llm_connector/models.py`

Keep the reshaped `LLMConfig` as is (fields: `user`, `default`, `provider`, `url`, `max_tokens`,
`api_key_encrypted`, `extra`, timestamps; `unique_together (user, provider)`) and add the manager
+ default exclusivity; drop `alias` from `LLMRequestLog`:

```python
from django.conf import settings
from django.db import models
from lukehirsch.managers import SystemScopedManager

from .crypto import decrypt, encrypt
from .validators import validate_safe_llm_url


class Provider(models.TextChoices):
    anthropic = "anthropic", "Anthropic"
    openai = "openai", "OpenAI"
    ollama = "ollama", "HirschAI"


class LLMConfig(models.Model):
    """One row per (user, provider): the provider credential + the default flag.

    The model is deliberately thin — WHICH model runs is a per-run pick from
    `llm_connector.catalog`, never stored here. `url`/`max_tokens`/`extra` exist for the
    system-owned HirschAI row (tower url, chat/embed model names, think flag) and are not
    exposed over the user API. Rows owned by the system user are the shared executors
    (today: HirschAI), same pattern as Domain/ApplicationLayout.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="llm_configs",
    )
    default = models.BooleanField(
        default=False,
        help_text="My runs go to this provider unless I pick another executor.",
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    url = models.URLField(blank=True)
    max_tokens = models.PositiveIntegerField(null=True, blank=True)
    api_key_encrypted = models.TextField(blank=True)
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SystemScopedManager()

    class Meta:
        unique_together = [("user", "provider")]
        ordering = ["user_id", "provider"]

    def __str__(self):
        return f"{self.user} / {self.provider}{' (default)' if self.default else ''}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # One default per user, enforced at the model so admin and API behave alike.
        if self.default:
            LLMConfig.objects.filter(user=self.user, default=True).exclude(
                pk=self.pk
            ).update(default=False)

    # api_key property / setter / has_api_key / to_config_dict / clean: unchanged
    # from the current file.
```

`LLMRequestLog`: delete the `alias = models.CharField(...)` field; everything else stays.

### 2. `lukehirsch/settings.py`

Replace the `LLM = {...}` block:

```python
# Seed values for the system-owned HirschAI row (llm_connector.conf.hirschai_row).
# The DB row is the runtime truth — edit it in the admin; these apply on first boot
# (or after a DB reset). Everything except "url" lands in the row's `extra`.
HIRSCHAI = {
    "url": os.getenv("HIRSCHAI_URL", "http://localhost:11434"),
    "model": os.getenv("HIRSCHAI_MODEL", "llama3.2:1b"),
    "embed_model": os.getenv("HIRSCHAI_EMBED_MODEL", "qwen3-embedding:0.6b"),
    "timeout": env_int("LLM_TIMEOUT", 300),
    "think": env_bool("LLM_THINKING", False),
}
```

`LLM_LOGGING`, `LLM_URL_ALLOWLIST`, `LLM_URL_ALLOW_PRIVATE`, `LLM_ENCRYPTION_KEY` stay. Clean
`LLM_DEFAULT_PROVIDER` / `LLM_URL` / `LLM_MODEL` / `LLM_EMBED_MODEL` / `LLM_STRENGTH` out of
`.env` at leisure — they are simply unread.

### 3. `llm_connector/catalog.py` (new)

```python
"""The curated commercial model catalog — the ONLY place model ids live.

Lukas maintains this by hand, so users never configure a model themselves and a
provider update is a one-line edit here. `default: True` marks the model auto-runs
use (exactly one per provider). Pricing metadata joins later (pricing-calculator
roadmap item) — keep the row shape open.
"""

CATALOG: dict[str, list[dict]] = {
    "anthropic": [
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "default": True},
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
    ],
    "openai": [
        # NOTE(lukas): verify current ids before first live run — you own this list.
        {"id": "gpt-5.1", "label": "GPT-5.1", "default": True},
        {"id": "gpt-5.1-mini", "label": "GPT-5.1 mini"},
    ],
}


def models_for(provider: str) -> list[dict]:
    return list(CATALOG.get(provider, ()))


def default_model(provider: str) -> str | None:
    for row in CATALOG.get(provider, ()):
        if row.get("default"):
            return row["id"]
    return None


def is_known_model(provider: str, model: str) -> bool:
    return any(row["id"] == model for row in CATALOG.get(provider, ()))
```

### 4. `llm_connector/probe.py` (new)

```python
"""Cached HirschAI reachability probe.

One cheap GET against Ollama's /api/tags with a short timeout, cached for
PROBE_MAX_AGE_S so the executors endpoint / auto-run checks don't hammer the tower.
`refresh=True` busts the cache (the live prompt tests use it)."""

import time
from urllib import request

from .conf import hirschai_row

PROBE_TIMEOUT_S = 2.0
PROBE_MAX_AGE_S = 30.0

_CACHE = {"ts": 0.0, "ok": False}


def hirschai_reachable(*, refresh: bool = False) -> bool:
    now = time.monotonic()
    if not refresh and now - _CACHE["ts"] < PROBE_MAX_AGE_S:
        return _CACHE["ok"]
    try:
        base = hirschai_row().url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")].rstrip("/")
        with request.urlopen(base + "/api/tags", timeout=PROBE_TIMEOUT_S) as resp:
            ok = resp.status == 200
    except Exception:  # noqa: BLE001 — any failure = not reachable
        ok = False
    _CACHE["ts"] = now
    _CACHE["ok"] = ok
    return ok
```

### 5. `llm_connector/conf.py` (rewrite)

```python
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# The tower executor's provider value. Its user-facing label is "HirschAI"
# (Provider.ollama.label); internally it IS ollama — the software, not the brand.
HIRSCHAI_PROVIDER = "ollama"


class ExecutorError(ValueError):
    """A caller-supplied executor (provider/model) doesn't resolve for this user.
    The API layers turn this into a 400."""


def hirschai_row():
    """The system-owned HirschAI config row, get_or_created from settings.HIRSCHAI.

    The DB row is the runtime truth (tweak url/models in the admin); the settings
    dict only seeds a missing row, so a fresh DB self-heals on first use.
    """
    from django.contrib.auth.models import User

    from .models import LLMConfig

    seed = getattr(settings, "HIRSCHAI", None)
    if not seed or "url" not in seed:
        raise ImproperlyConfigured(
            "settings.HIRSCHAI (with at least a 'url') is required — it seeds the "
            "system HirschAI row."
        )
    system, _ = User.objects.get_or_create(
        username=settings.SYSTEM_USER_USERNAME, defaults={"is_active": False}
    )
    row, _ = LLMConfig.objects.get_or_create(
        user=system,
        provider=HIRSCHAI_PROVIDER,
        defaults={
            "url": seed["url"],
            "extra": {k: v for k, v in seed.items() if k != "url"},
        },
    )
    return row


def resolve_config(provider: str | None = None, *, user=None, model: str | None = None) -> dict:
    """Adapter config dict for an executor.

    - provider None  -> the user's `default` commercial row, else the HirschAI row
                        (the "no personal config" flows).
    - provider ollama -> the HirschAI system row (users never own tower rows).
    - commercial     -> the user's row for that provider; missing row raises
                        ExecutorError (the API validates upstream — reaching this
                        from a view is a caller bug).
    `model` (the per-run catalog pick) overrides the dict's model; when absent the
    catalog default fills in for commercial providers.
    """
    from .models import LLMConfig

    if provider is None:
        row = (
            LLMConfig.objects.filter(user=user, default=True).first()
            if user is not None
            else None
        )
        if row is not None and row.provider != HIRSCHAI_PROVIDER:
            return _finish(row.to_config_dict(), row.provider, model)
        return _finish(hirschai_row().to_config_dict(), HIRSCHAI_PROVIDER, model)
    if provider == HIRSCHAI_PROVIDER:
        return _finish(hirschai_row().to_config_dict(), provider, model)
    row = LLMConfig.objects.filter(user=user, provider=provider).first()
    if row is None:
        raise ExecutorError(f"No {provider!r} config — add an API key first.")
    return _finish(row.to_config_dict(), provider, model)


def _finish(config: dict, provider: str, model: str | None) -> dict:
    config["provider"] = provider
    if model:
        config["model"] = model
    if not config.get("model"):
        from .catalog import default_model

        fallback = default_model(provider)
        if fallback:
            config["model"] = fallback
    return config


def resolve_executor(user, provider: str = "", model: str = ""):
    """Validate + build the Executor a request names. Raises ExecutorError with a
    user-facing message on anything that shouldn't run; the jac serializer/views
    map that to a 400. Blank provider -> the user's default executor."""
    from .catalog import default_model, is_known_model
    from .executor import Executor

    provider = (provider or "").strip()
    model = (model or "").strip()
    if not provider:
        ex = default_executor(user)
        if ex is None:
            raise ExecutorError(
                "No executor available — HirschAI is offline and no provider is configured."
            )
        return Executor(ex.provider, model or ex.model, user)
    if provider == HIRSCHAI_PROVIDER:
        # The tower's model is fixed server-side; a client-sent model is ignored.
        return Executor(HIRSCHAI_PROVIDER, None, user)
    from .models import LLMConfig, Provider

    if provider not in Provider.values:
        raise ExecutorError(f"Unknown provider {provider!r}.")
    row = LLMConfig.objects.filter(user=user, provider=provider).first()
    if row is None or not row.has_api_key:
        raise ExecutorError(f"No {provider!r} API key configured.")
    model = model or default_model(provider) or ""
    if not is_known_model(provider, model):
        raise ExecutorError(f"Unknown model {model!r} for {provider!r}.")
    return Executor(provider, model, user)


def default_executor(user):
    """The executor auto-runs use: the user's default commercial row (with a stored
    key), else HirschAI when reachable, else None ("manual only" — the SPA offers
    hand-curation). Returns an Executor or None."""
    from .executor import Executor
    from .models import LLMConfig
    from .probe import hirschai_reachable

    row = LLMConfig.objects.filter(user=user, default=True).first()
    if row is not None and row.provider != HIRSCHAI_PROVIDER and row.has_api_key:
        from .catalog import default_model

        return Executor(row.provider, default_model(row.provider), user)
    if hirschai_reachable():
        return Executor(HIRSCHAI_PROVIDER, None, user)
    return None


def get_embed_floors() -> dict:
    """Per-section cosine drop floors from the HirschAI row's `embed_floors` extra.
    {} when unset/broken. Floors are an embedder property; there is exactly one
    embedder (the tower), so this needs no arguments anymore."""
    try:
        floors = hirschai_row().to_config_dict().get("embed_floors")
    except Exception:  # noqa: BLE001 — missing/broken row -> defaults
        return {}
    return floors if isinstance(floors, dict) else {}


def logging_enabled() -> bool:
    return bool(getattr(settings, "LLM_LOGGING", False))
```

Deleted with no replacement: `FALLBACK_ALIAS`, `get_llm_settings`, `_settings_config`,
`_global_fallback`, `get_alias_config`, `FREE_PROVIDERS`, `is_free_alias`, `get_pinned_alias`,
`pick_alias`. `LLMPin` never ships (the guide-2-era model class is dead code — delete it if
present).

### 6. `llm_connector/executor.py` (new)

```python
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

    def _client(self) -> LLMClient:
        return LLMClient(self.provider, user=self.user, model=self.model)

    def complete(self, prompt=None, *, messages=None, **kwargs) -> str:
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
```

### 7. `llm_connector/client.py`

Only the constructor and the log writer change (retry/reporter machinery untouched):

```python
    def __init__(self, provider: str | None = None, *, user=None, model: str | None = None):
        """provider None -> the user's default executor (their default commercial
        row, else HirschAI). `model` overrides the resolved config's model."""
        self.user = user
        self._config = resolve_config(provider, user=user, model=model)
        self.provider = self._config["provider"]
        self.model = self._config.get("model", "")
        adapter_cls = get_adapter_class(self.provider)
        self._adapter = adapter_cls(self._config)
```

Import swap at the top: `from .conf import logging_enabled, resolve_config`. In `_write_log`,
drop `alias=self.alias` and use `provider=self.provider, model=self.model`.

### 8. `llm_connector/__init__.py`

```python
from .client import LLMClient
from .conf import HIRSCHAI_PROVIDER


def get_client(provider: str | None = None, *, user=None, model: str | None = None) -> LLMClient:
    return LLMClient(provider, user=user, model=model)


def complete(prompt=None, *, messages=None, provider=None, model=None, user=None, **kwargs) -> str:
    return get_client(provider, user=user, model=model).complete(
        prompt=prompt, messages=messages, **kwargs
    )


def stream(prompt=None, *, messages=None, provider=None, model=None, user=None, **kwargs):
    return get_client(provider, user=user, model=model).stream(
        prompt=prompt, messages=messages, **kwargs
    )


def embed(inputs: list[str]) -> list[list[float]]:
    """Embed on the tower. Embedding is a HirschAI-only capability by design:
    commercial executors never see embedding work (and never could route it here
    without an explicit call site — the privacy grep in Verification checks that)."""
    return get_client(HIRSCHAI_PROVIDER).embed(inputs)
```

(`web_search` / `can_web_search` module helpers die — `Executor.web_search` /
`.supports_web_search` are the interface now.)

### 9. `llm_connector/serializers.py`

```python
class LLMConfigSerializer(serializers.ModelSerializer):
    """One credential per commercial provider. `api_key` is write-only; omitting it
    on PATCH keeps the stored key. The tower is not configurable here — it is the
    operator's system row."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={"input_type": "password"},
    )
    has_api_key = serializers.BooleanField(read_only=True)

    class Meta:
        model = LLMConfig
        fields = ("id", "user", "provider", "default", "api_key", "has_api_key",
                  "created_at", "updated_at")
        read_only_fields = ("id", "has_api_key", "created_at", "updated_at")
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=LLMConfig.objects.all(), fields=("user", "provider")
            )
        ]

    def validate_provider(self, value):
        if value == Provider.ollama:
            raise serializers.ValidationError(
                "HirschAI is built in — configure commercial providers only."
            )
        return value

    # create()/update(): keep the current api_key pop/encrypt pattern verbatim.
```

`LLMRequestLogSerializer`: drop `"alias"` from `fields`.

### 10. `llm_connector/views.py`

Delete `LLMPinView`, `LLMAliasListView`, `_adapter_capabilities`. `LLMConfigViewSet` keeps its
scoping; the `check` action resolves by row + catalog default:

```python
    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        config = self.get_object()
        try:
            client = LLMClient(config.provider, user=request.user)
            start = time.monotonic()
            client.complete("Respond with exactly one word: pong")
            latency_ms = int((time.monotonic() - start) * 1000)
            return Response({"ok": True, "latency_ms": latency_ms})
        except Exception as exc:  # noqa: BLE001 — any failure is the check's finding
            return Response({"ok": False, "error": str(exc)})
```

New view:

```python
class ExecutorListView(APIView):
    """Everything the generate panel needs in ONE request: HirschAI (with a live
    reachability flag) + every catalog provider (configured?, default?, models).
    `modes` are jac vocabulary served as opaque labels — deliberate denormalisation
    so the SPA needs no second endpoint; `high` is commercial-only by design."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        own = {c.provider: c for c in LLMConfig.objects.filter(user=request.user)}
        commercial_default = any(
            c.default and c.has_api_key for c in own.values()
        )
        rows = [
            {
                "provider": HIRSCHAI_PROVIDER,
                "label": "HirschAI",
                "self_hosted": True,
                "configured": True,
                "reachable": hirschai_reachable(),
                "default": not commercial_default,
                "models": [],
                "modes": ["standard"],
            }
        ]
        for provider in CATALOG:
            row = own.get(provider)
            rows.append(
                {
                    "provider": provider,
                    "label": Provider(provider).label,
                    "self_hosted": False,
                    "configured": bool(row and row.has_api_key),
                    "reachable": None,
                    "default": bool(row and row.default and row.has_api_key),
                    "models": models_for(provider),
                    "modes": ["standard", "high"],
                }
            )
        return Response(rows)
```

Imports: `from llm_connector.catalog import CATALOG, models_for`,
`from llm_connector.conf import HIRSCHAI_PROVIDER`, `from llm_connector.models import Provider`,
`from llm_connector.probe import hirschai_reachable`.

### 11. `llm_connector/urls.py`

```python
urlpatterns = [
    path("executors/", ExecutorListView.as_view(), name="llmexecutor-list"),
    *router.urls,
]
```

### 12. `llm_connector/admin.py`

`LLMConfigAdminForm.Meta.fields`: `("user", "default", "provider", "url", "max_tokens",
"extra", "api_key")` (as already edited). `LLMConfigAdmin.list_display = ("user", "provider",
"default", "api_key_set", "updated_at")`; `list_filter = ("provider", "default")`;
`search_fields = ("user__username", "user__email")`. `LLMRequestLogAdmin`: remove the stray
`"def"` entry and every `alias` mention from `list_display` / `list_filter` / `search_fields` /
`readonly_fields`.

### 13. `llm_connector/management/commands/llm_check.py`

Reshape to executors (keep the output style):

```python
class Command(BaseCommand):
    help = "Round-trip check: HirschAI (probe + pong) and a user's configured providers."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, help="Also check this user's provider rows.")

    def handle(self, *args, **opts):
        reachable = hirschai_reachable(refresh=True)
        self.stdout.write(f"HirschAI: {'reachable' if reachable else 'OFFLINE'}")
        if reachable:
            self._pong(Executor(HIRSCHAI_PROVIDER))
        if opts.get("user"):
            user = self._get_user(opts["user"])  # raise CommandError on unknown pk
            for row in LLMConfig.objects.filter(user=user):
                self._pong(Executor(row.provider, default_model(row.provider), user))

    def _pong(self, executor):
        start = time.monotonic()
        try:
            executor.complete("Respond with exactly one word: pong")
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(f"  {executor.provider}: FAILED — {exc}")
            return
        ms = int((time.monotonic() - start) * 1000)
        self.stdout.write(f"  {executor.provider} model={executor.model or '(row default)'} {ms}ms")
```

## Tests — on disk, red now

Rewritten by the AI against the target API (red until this guide is typed):

- `llm_connector/tests/_helpers.py` — `FakeAdapter` grows `embed()` + a `SearchyFakeAdapter`
  (`supports_web_search=True`, registered as `fakesearch`); `FAKE_LLM` settings dict dies.
- `llm_connector/tests/test_config.py` — crypto (kept), model shape (unique per provider,
  single-default `save()`, ollama-url `clean()`), `hirschai_row` bootstrap/idempotency,
  `resolve_config` / `resolve_executor` / `default_executor` matrices, catalog helpers,
  probe caching, `Executor` behavior, request-log attribution.
- `llm_connector/tests/test_client.py` — message normalisation + registry (kept), client keyed
  on provider/model (per-call model override), retry + reporter (kept, new construction),
  logging rows carry provider/model.
- `llm_connector/tests/test_api.py` — configs CRUD (ollama rejected, duplicate provider 400,
  default exclusivity through the API, key write-only), executors endpoint shape (probe mocked),
  check action, request-log scoping, unauth 403s.
- `llm_connector/tests/test_adapters.py` — Google adapter tests deleted (provider is gone);
  Anthropic/OpenAI/Ollama adapter tests untouched.

## Verification

1. `python manage.py makemigrations llm_connector && python manage.py migrate` on a fresh DB
   (jac migrations land with guide 2 — expect jac to still be import-broken until then; run the
   connector suite alone: `python manage.py test llm_connector`).
2. Connector suite green — clean wall of dots.
3. Dead-vocabulary grep, empty outside `migrations/`:
   ```bash
   grep -rn "get_alias_config\|pick_alias\|get_pinned_alias\|is_free_alias\|FALLBACK_ALIAS\|LLMPin\|FREE_PROVIDERS\|settings.LLM\b\|LLM\[" backend --include="*.py" | grep -v migrations
   grep -rniE "\balias\b" backend/llm_connector --include="*.py" | grep -v migrations
   ```
4. Live: `python manage.py llm_check` with ollama up prints `HirschAI: reachable` + a pong
   latency; `curl` the executors endpoint as a logged-in user and check the three rows.

## Results

<!-- Human fills this in: raw test output, observed issues, what works. -->
