import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# The tower executor's provider value. Its user-facing label is "HirschAI"
# (Provider.ollama.label); internally it IS ollama — the software, not the brand.
HIRSCHAI_PROVIDER = "ollama"


class ExecutorError(ValueError):
    """A caller-supplied executor (provider/model) doesn't resolve for this user.
    The API layers turn this into a 400. `provider` names the key it was about, when one."""

    def __init__(self, message: str, provider: str = ""):
        super().__init__(message)
        self.provider = provider


def hirschai_row():
    """The system-owned HirschAI config row, get_or_created from settings.HIRSCHAI.

    The row carries what the admin tweaks (embed_floors, think, …); the settings dict
    only seeds a missing row, so a fresh DB self-heals on first use. What this process's
    environment names wins over it on every call: see `_hirschai_config`.
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


def _hirschai_config() -> dict:
    """The row's config under the environment's URL + key. Per process, because web and worker
    may carry different keys for one model server, and moving the app has to mean changing env,
    not editing a row."""
    config = hirschai_row().to_config_dict()
    config.update(getattr(settings, "HIRSCHAI_ENV", {}))
    return config


def _label(provider: str) -> str:
    from .models import Provider

    return dict(Provider.choices).get(provider, provider)


def require_usable(row, provider: str):
    """The row itself if data may go out on it (`LLMConfig.usable`). Raises ExecutorError with
    a user-facing message otherwise: a key without the consent stamp counts as unconfigured,
    and is told how to fix that."""
    if row is None or not row.has_api_key:
        raise ExecutorError(f"No {provider!r} API key configured.")
    if not row.usable:
        raise ExecutorError(
            f"Your {_label(provider)} key has no consent to the data transfer yet: save the "
            "key again and agree to the transfer."
        )
    return row


def _usable_row(user, provider: str):
    """The user's row for a commercial provider, under `require_usable`."""
    from .models import LLMConfig

    return require_usable(
        LLMConfig.objects.filter(user=user, provider=provider).first(), provider
    )


def _default_row(user):
    """The user's default commercial row if data may go out on it, else None (HirschAI)."""
    from .models import LLMConfig

    if user is None:
        return None
    row = LLMConfig.objects.filter(user=user, default=True).first()
    if row is not None and row.provider != HIRSCHAI_PROVIDER and row.usable:
        return row
    return None


def _pick_model(provider: str, models, model: str | None) -> dict:
    """The stored entry a run on this key uses: the one it names, else the default pick. A key
    without a list, or a model it doesn't reach, is the user's to fix with Check: nothing runs
    on another model or executor instead."""
    from .catalog import default_model, find_model

    label = _label(provider)
    if not models:
        raise ExecutorError(
            f"Your {label} key has no model list yet: press Check on it under "
            "Account → LLM providers.",
            provider,
        )
    model = model or default_model(provider, models)
    entry = find_model(models, model)
    if entry is None:
        raise ExecutorError(
            f"Unknown model {model!r} for {provider!r}: Check on your {label} key under "
            "Account → LLM providers refreshes its models.",
            provider,
        )
    return entry


def resolve_config(
    provider: str | None = None, *, user=None, model: str | None = None
) -> dict:
    """Adapter config dict for an executor.

    - provider None  -> the user's `default` commercial row if it's usable, else the
                        HirschAI row (the "no personal config" flows), like
                        `default_executor`.
    - provider ollama -> the HirschAI system row (users never own tower rows).
    - commercial     -> the user's usable row for that provider; a missing row, a
                        missing key or a missing consent raises ExecutorError (the API
                        validates upstream). This is where the key leaves the database,
                        so every call on a user's key passes this gate.
    `model` (the per-run pick) overrides the dict's model. On a commercial key it must be one
    the key's stored list holds, and absent it the default pick from that list fills in; the
    entry's private fields (how the model thinks) travel to the adapter with it.
    """
    if provider is None:
        row = _default_row(user)
        if row is not None:
            return _finish(row.to_config_dict(), row.provider, model, row.models)
        return _finish(_hirschai_config(), HIRSCHAI_PROVIDER, model)
    if provider == HIRSCHAI_PROVIDER:
        return _finish(_hirschai_config(), provider, model)
    row = _usable_row(user, provider)
    return _finish(row.to_config_dict(), provider, model, row.models)


def _finish(config: dict, provider: str, model: str | None, models=None) -> dict:
    from .catalog import COMMERCIAL_PROVIDERS

    config["provider"] = provider
    if provider in COMMERCIAL_PROVIDERS:
        entry = _pick_model(provider, models, model)
        config["model"] = entry["id"]
        if entry.get("thinking"):
            config["thinking"] = entry["thinking"]
    elif model:
        config["model"] = model
    return config


def resolve_executor(user, provider: str = "", model: str = ""):
    """Validate + build the Executor a request names. Raises ExecutorError with a
    user-facing message on anything that shouldn't run; the jac serializer/views
    map that to a 400. Blank provider -> the user's default executor's, under the same checks."""
    from .catalog import COMMERCIAL_PROVIDERS
    from .executor import Executor

    provider, model = provider or "", model or ""
    if not isinstance(provider, str) or not isinstance(model, str):
        raise ExecutorError("provider and model must be text.")
    provider, model = provider.strip(), model.strip()
    if not provider:
        row = _default_row(user)
        provider = row.provider if row is not None else HIRSCHAI_PROVIDER
    if provider == HIRSCHAI_PROVIDER:
        # The tower's model is fixed server-side; a client-sent model is ignored.
        return Executor(HIRSCHAI_PROVIDER, None, user)
    if provider not in COMMERCIAL_PROVIDERS:
        raise ExecutorError(f"Unknown provider {provider!r}.")
    row = _usable_row(user, provider)
    return Executor(provider, _pick_model(provider, row.models, model)["id"], user)


def default_executor(user):
    """The executor auto-runs use: the user's default commercial row (with a stored
    key and the consent to the transfer) on the default pick from its model list, else
    HirschAI. A default key without a list raises ExecutorError rather than running on the
    tower instead. There is no reachability gate on purpose: a model server that is asleep is
    normal, and a queued run simply waits for it."""
    from .executor import Executor

    row = _default_row(user)
    if row is None:
        return Executor(HIRSCHAI_PROVIDER, None, user)
    return Executor(row.provider, _pick_model(row.provider, row.models, "")["id"], user)


def executor_knobs(executor) -> dict:
    """The knob spec of the executor's model, from the user's stored list. {} for HirschAI:
    the tower model is operator-tuned."""
    from .catalog import knobs_for
    from .models import LLMConfig

    if executor.is_hirschai or not executor.model:
        return {}
    row = LLMConfig.objects.filter(
        user=executor.user, provider=executor.provider
    ).first()
    return knobs_for(row.models if row else [], executor.model)


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


def log_bodies() -> bool:
    """Store prompt + response text in the log row? Off in prod (settings.LLM_LOG_BODIES)."""
    return bool(getattr(settings, "LLM_LOG_BODIES", False))
