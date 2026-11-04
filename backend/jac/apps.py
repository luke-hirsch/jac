from django.apps import AppConfig


class JacConfig(AppConfig):
    name = "jac"

    def ready(self):
        from jac import ai_jobs, signals  # noqa: F401 — ai_jobs registers its handlers

        signals.connect()
