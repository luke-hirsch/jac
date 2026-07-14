from django.apps import AppConfig


class JacConfig(AppConfig):
    name = "jac"

    def ready(self):
        from jac import signals

        signals.connect()
