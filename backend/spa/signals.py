"""spa.signals — session-level signal handlers for the spa app."""

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from spa.models import PersonalityProfile, UserProfile


def on_mfa_authenticator_used(sender, request, user, authenticator, **kwargs):
    """Mark the session as MFA-authenticated after any successful authenticator use."""
    request.session["mfa_authenticated"] = True
    request.session.modified = True


@receiver(post_save, sender=get_user_model())
def _create_profile_on_user_creation(sender, instance, created, **kwargs):
    if created:
        from spa.portfolio import mint_handle

        UserProfile.objects.create(user=instance, handle=mint_handle(instance.username))
        PersonalityProfile.objects.create(user=instance)
