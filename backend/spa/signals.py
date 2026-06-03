"""spa.signals — session-level signal handlers for the spa app."""


def on_mfa_authenticator_used(sender, request, user, authenticator, **kwargs):
    """Mark the session as MFA-authenticated after any successful authenticator use."""
    request.session["mfa_authenticated"] = True
    request.session.modified = True
