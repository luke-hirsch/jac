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


def resolve_config(
    provider: str | None = None, *, user=None, model: str | None = None
) -> dict:
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
