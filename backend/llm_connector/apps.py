from django.apps import AppConfig


class LLMConnectorConfig(AppConfig):
    name = "llm_connector"
    verbose_name = "LLM Connector"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        import warnings

        from .conf import FALLBACK_ALIAS, get_llm_settings

        try:
            llm = get_llm_settings()
        except Exception as exc:
            warnings.warn(str(exc), stacklevel=2)
            return

        if FALLBACK_ALIAS not in llm:
            warnings.warn(
                f"settings.LLM[{FALLBACK_ALIAS!r}] is missing — required as "
                "the zero-cost fallback for users without a personal LLMConfig.",
                stacklevel=2,
            )
