from django.apps import AppConfig


class LLMConnectorConfig(AppConfig):
    name = "llm_connector"
    verbose_name = "LLM Connector"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        import warnings

        from django.conf import settings

        seed = getattr(settings, "HIRSCHAI", None)
        if not seed or "url" not in seed:
            warnings.warn(
                "settings.HIRSCHAI (with at least a 'url') is missing — it seeds "
                "the system HirschAI row, the zero-cost fallback for users without "
                "a personal LLMConfig.",
                stacklevel=2,
            )
