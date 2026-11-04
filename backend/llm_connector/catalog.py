"""The curated commercial model catalog — the ONLY place model ids live."""

# models ...

CATALOG: dict[str, list[dict]] = {
    "anthropic": [
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "default": True},
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
    ],
    "openai": [
        {"id": "gpt-5.6-terra", "label": "Terra", "default": True},
        {"id": "gpt-5.6-sol", "label": "Sol"},
        {"id": "gpt-5.6-luna", "label": "Luna"},
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


# ... and their modifications

KNOBS: dict[str, dict[str, dict]] = {
    "anthropic": {
        "effort": {"choices": ["low", "medium", "high"]},
        "temperature": {"min": 0.0, "max": 1.0, "excludes": ["effort"]},
    },
    "openai": {
        "effort": {"choices": ["low", "medium", "high"]},
    },
}


def knobs_for(provider: str) -> dict:
    return {name: dict(spec) for name, spec in KNOBS.get(provider, {}).items()}


def validate_params(provider: str, params) -> list[str]:
    """Knob values against the spec — a list of user-facing problems, [] = valid."""
    if not isinstance(params, dict):
        return ["Expected an object of knob values."]
    if not params:
        return []
    spec = KNOBS.get(provider)
    if not spec:
        return [f"No knobs for {provider!r} — the tower model is operator-tuned."]
    problems = []
    for name, value in params.items():
        knob = spec.get(name)
        if knob is None:
            problems.append(f"Unknown knob {name!r} for {provider!r}.")
            continue
        if "choices" in knob and value not in knob["choices"]:
            problems.append(f"{name} must be one of: {', '.join(knob['choices'])}.")
        if "min" in knob and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not knob["min"] <= value <= knob["max"]
        ):
            problems.append(
                f"{name} must be a number between {knob['min']} and {knob['max']}."
            )
        for other in knob.get("excludes", ()):
            if other in params:
                problems.append(f"{name} and {other} cannot be combined.")
    return problems
