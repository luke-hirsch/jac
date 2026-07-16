"""The curated commercial model catalog — the ONLY place model ids live."""

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
