from django.apps import AppConfig


class SpaConfig(AppConfig):
    name = "spa"

    def ready(self):
        from allauth.mfa import signals as mfa_signals
        from spa.signals import on_mfa_authenticator_used

        mfa_signals.authenticator_used.connect(on_mfa_authenticator_used)
