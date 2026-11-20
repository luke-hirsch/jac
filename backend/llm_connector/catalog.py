"""Which providers are commercial, and the rules over a key's model list.

No model id lives in code. Each user's LLMConfig row stores what its key reaches, as the
provider listed it (`refresh_models`: when the key is saved, and on Check); the adapters say how
to call each model. Everything here reads that stored list, never the provider.
"""

from django.utils import timezone

# The providers a user brings their own key for, in the order the SPA lists them.
COMMERCIAL_PROVIDERS = ("anthropic", "openai")

# Listing runs on a request thread (saving a key, Check): short, and a single try.
REFRESH_TIMEOUT_S = 10.0

# The pick when a run names no model, newest first within it: a mid-size model that writes well.
_DEFAULT_FAMILY = {
    "anthropic": lambda model_id: "sonnet" in model_id,
    "openai": lambda model_id: model_id.startswith("gpt-"),
}


def find_model(models, model_id) -> dict | None:
    """The stored entry for `model_id`, or None."""
    for entry in models or ():
        if entry.get("id") == model_id:
            return entry
    return None


def is_known_model(models, model_id) -> bool:
    return find_model(models, model_id) is not None


def default_model(provider: str, models) -> str | None:
    """The newest model of the provider's default family, else the newest of all, else None
    (the key has no list yet)."""
    ids = [entry["id"] for entry in models or () if entry.get("id")]
    in_family = _DEFAULT_FAMILY.get(provider)
    if in_family is not None:
        for model_id in ids:
            if in_family(model_id):
                return model_id
    return ids[0] if ids else None


def knobs_for(models, model_id) -> dict:
    """The knob spec of one stored model: {} for an unknown one."""
    knobs = (find_model(models, model_id) or {}).get("knobs") or {}
    return {name: dict(spec) for name, spec in knobs.items()}


def picker_models(provider: str, models) -> list[dict]:
    """The stored list as the executors endpoint serves it: id, label, the model's knobs, and
    `default` on the one a run without a pick gets. Private adapter fields stay behind."""
    default = default_model(provider, models)
    rows = []
    for entry in models or ():
        row = {
            "id": entry["id"],
            "label": entry.get("label") or entry["id"],
            "knobs": entry.get("knobs") or {},
        }
        if entry["id"] == default:
            row["default"] = True
        rows.append(row)
    return rows


def validate_params(knobs: dict, params) -> list[str]:
    """Knob values against one model's spec — a list of user-facing problems, [] = valid."""
    if not isinstance(params, dict):
        return ["Expected an object of knob values."]
    if not params:
        return []
    if not knobs:
        return ["This model takes no knobs."]
    problems = []
    for name, value in params.items():
        knob = knobs.get(name)
        if knob is None:
            problems.append(f"This model has no {name!r} knob.")
            continue
        choices = knob.get("choices") or []
        if value not in choices:
            problems.append(f"{name} must be one of: {', '.join(choices)}.")
    return problems


def refresh_models(row) -> list[dict]:
    """Ask the provider which models this key reaches, and store the list on the row.

    Only a row data may go out on gets asked (the same gate as every call on a user's key).
    Raises what the listing raised, and the row keeps its last list then: a provider having a
    bad minute must not take the models away. The callers decide what a failure means.
    """
    from .conf import require_usable
    from .registry import get_adapter_class

    require_usable(row, row.provider)
    config = row.to_config_dict()
    config.update(timeout=REFRESH_TIMEOUT_S, max_retries=0)
    models = get_adapter_class(row.provider)(config).list_models()
    row.models = models
    row.models_checked_at = timezone.now()
    row.save(update_fields=["models", "models_checked_at"])
    return models
